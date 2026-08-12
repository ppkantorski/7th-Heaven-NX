#!/usr/bin/env python3
"""
7th Heaven NX -- apply 7th Heaven .iro mods to the Switch version of FF7.

Layout expected in this script's directory:

    7th_heaven_nx.py
    dump/                your Switch game dump, as ripped:
                           dump/romfs/ff7/workingdir/   the LGP archives
                           dump/romfs/ff7/resources/    the x86 exe
                           dump/exefs/main              the ARM64 module
                         (a bare workingdir/ here still works too)
    mods/                .iro files
    cache/               created automatically, extracted mods
    sdout/               created automatically, copy onto your SD card

Run with no arguments for the UI, or --cli to build with saved settings.

Copyright (c) 2026 ppkantorski
"""
import json
import os
import queue
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))

# Shown in the header, nearest the title. Single source of truth --
# anything else that needs it should import this rather than repeat it.
VERSION = '1.0.0'
sys.path.insert(0, HERE)

import build  # noqa: E402
import build_guard  # noqa: E402
import iro  # noqa: E402
import lgp    # noqa: E402
import ff7nx_field169  # noqa: E402

# Resolved once, at import: $SEVENTH_NX_DUMP, then dump/, then the old
# workingdir/, then any subfolder that contains romfs/ff7/workingdir.
DUMP = build.find_game_dump(HERE)
WORKINGDIR = DUMP.workingdir if DUMP else os.path.join(HERE, 'workingdir')
MODS_DIR = os.path.join(HERE, 'mods')
CACHE_DIR = os.path.join(HERE, 'cache')
SDOUT_DIR = os.path.join(HERE, 'sdout')
SETTINGS = os.path.join(HERE, 'settings.json')


def _global_setting(key, default):
    """
    One `__global__` key straight out of settings.json.

    Used for controls that have no widget yet. The build reads its knobs from
    the environment, and the two places that populate it -- start() and the
    headless path -- both sit outside the dialog's scope, so neither can reach
    a Tk variable. Reading the file is the only thing both can do, and it
    keeps settings.json the single source of truth rather than adding a second
    one the user has to know about.
    """
    try:
        with open(SETTINGS) as fh:
            return json.load(fh).get('__global__', {}).get(key, default)
    except Exception:                                           # noqa: BLE001
        return default



# ---------------------------------------------------------------- load order
#
# Mirrors the official 7th Heaven client so conflicts resolve the same way:
#
#   AppCore/ModLoadOrder.cs        category -> integer
#   AppUI/.../MyModsViewModel.cs   AutoSortBasedOnCategory():
#                                    OrderBy(category).ThenBy(name)
#   AppWrapper/VFile.cs            MapFile() returns the FIRST override
#                                    found for a file
#
# First match wins, and the sort is ascending, so the LOWEST category number
# takes priority. Animations (1) therefore overrides Battle Models (2) and
# Field Models (4) -- which is what makes the 60 FPS mod's interpolated
# animations win against Ninostyle rather than the other way round.
#
# build.build_plan() applies its input later-wins, so run_build() hands it
# this order reversed.

# Field/world texture dimension cap -- see build.FIELD_TEX_CAP_ENV. Off by
# default (0): the field module is fine with full-size mod textures as
# shipped. This exists for mod sets that push per-model textures well past
# what's been tested, where a scene loading many distinct NPC models at
# once can outrun available RAM -- the fix is fewer/smaller pixels loaded
# at a time, not a code change, so it's exposed as a build choice rather
# than a fixed constant.
FIELD_TEX_CAP_CHOICES = [
    (0, 'Off \u2014 full size, as shipped'),
    (1024, 'Cap at 1024px'),
    (768, 'Cap at 768px'),
    (512, 'Cap at 512px'),
    (256, 'Cap at 256px'),
    (128, 'Cap at 128px'),
]

# Battle background texture cap -- see build.BATTLE_BG_TEX_CAP_ENV. Scoped
# to ONLY the tiles this tool synthesizes from Avalanche Arisen's DDS mod
# (matched to their real battle.lgp entries via battle_bg_dds_map.json);
# every other battle.lgp/magic.lgp texture -- vanilla or any other mod's
# character/enemy skins -- always stays at the 256px hardware-proven size,
# regardless of this setting.
#
# Unlike the field cap, there is no "off" state: the battle module always
# needs some target resolution for the paletted conversion, and 256px is
# the default (what every prior build already used) rather than an opt-in
# override -- so leaving this untouched changes nothing. The mod's own art
# tops out at 1024px per tile; whether the battle module's texture bind
# path renders correctly above 256px is unverified on hardware, which is
# exactly what this setting is for finding out.
BATTLE_BG_TEX_CAP_CHOICES = [
    (256, 'Cap at 256px (default, hardware-proven)'),
    (512, 'Cap at 512px'),
    (768, 'Cap at 768px'),
    (1024, 'Cap at 1024px \u2014 full size, as authored'),
]

# SUPERSEDED by FIELD_BG_PAGE_PX_CHOICES below. Kept only so an older
# settings.json still loads. Everything it says about the FORMAT is
# right; the conclusion -- "there is no dimension to resize" -- is not.
#
# Field background budget -- see build.FIELD_BG_CAP_ENV. This is the cap for
# mods that replace flevel.lgp background sections; Cosmos Limit Break is the
# one that makes it matter, replacing 683 of the game's 741 fields.
#
# It is measured in 256x256 texture pages, not pixels, and that is not a
# shortcut: a field background is stored as up to 42 fixed 256x256 indexed
# pages with every tile sprite addressing a (page, x, y) inside them, so
# there is no dimension to resize -- see the FIELD_BG_CAP_ENV comment in
# build.py. Pages ARE the memory: 64 KB each, and the widescreen-extended
# backgrounds pay for the wider image by using more of them (vanilla
# nivl_b22 uses 8, the mod's 12).
#
# Off by default. A field over budget keeps its Switch-vanilla background --
# correct, just not extended -- so raising and lowering this is always safe
# to try. Cosmos Limit Break's fields run 1-15 pages, averaging 5, so a cap
# of 12 holds back 9 of its 683 fields, 10 holds back 20, and 8 holds
# back 46.
FIELD_BG_CAP_CHOICES = [
    (0, 'Off \u2014 as shipped'),
    (16, 'Cap at 16 pages (1.0 MB/field)'),
    (12, 'Cap at 12 pages (768 KB/field)'),
    (10, 'Cap at 10 pages (640 KB/field)'),
    (8, 'Cap at 8 pages (512 KB/field)'),
]

# Field background page SIZE -- the control that replaces the page-count cap
# above, which was inert (Cosmos Limit Break's fields run 1-15 pages against
# a 42-page ceiling, so no setting the dropdown offered ever held a field
# back).
#
# 256x256 is not a format constant after all. It is six immediates in
# `exefs/main` -- the loader's read size, the allocator's element size, the
# pre-allocator's, the pixel-fixup loop bound and the surface descriptor's
# width/height and stride -- and all six can be moved. See
# ff7nx_fieldbg.py and README-field-bg-512-MEASURED.md.
#
# Scoped to TRUECOLOR (depth 2) pages only, on purpose. The loader's
# #0x10000 is shared between the depth-1 allocation, the depth-1 READ and
# the depth-2 allocation; raising it would make every 8-bit page read four
# times too many bytes and desynchronise the stream. Scaling the depth-2
# element size instead leaves it alone, so paletted pages keep 256x256 and a
# field can hold both at once.
#
# Which means: with the mod's backgrounds still 8-bit paletted, 512px is a
# CORRECTNESS TEST, not a visible upgrade. It rescales the 51 truecolor
# pages vanilla ships and nothing else, and the game should look identical.
# That is the point -- it proves the module patch on hardware before any art
# depends on it. Turning the mod's paletted pages into truecolor needs the
# repack described at the end of README-field-bg-512-MEASURED.md.
#
# Needs a full game dump (the patch is in exefs/main). Off by default.
# The ladder is DERIVED, not chosen. Two independent gates gave it:
#
#   1. The module. The loader reads exactly px*px*2 bytes per depth-2 page at
#      +0x9370CC, and that immediate has to fit in ONE instruction. Brute-
#      forcing the encoder over every multiple of 16 admits 16..176 and then
#      256/512/768/1024, and nothing in between -- 384 (0x48000) and 480
#      (0x70800) are neither movz-shiftable nor bitmask immediates. HANDOFF-52
#      3.1 blamed the element size for this; that word is an ALLOCATION size
#      and rounds up, so it was never the constraint.
#   2. field_bg_native.resize_depth2, which rescales the 51 vanilla depth-2
#      pages (27 fields, measured off flevel.lgp) and only does INTEGER
#      ratios against 256. That admits 128 (2:1 down) and the multiples of
#      256 (k:1 up). 144/160/176 would need a general resampler to buy
#      0.15-0.18 MB a page against 128's 0.09.
#
# Cost per page is 6*px^2 -- the pixels PLUS the 32bpp surface the engine
# builds from them (x86 0x63FAAB). Vanilla paletted 256 costs 0.31 MB, and
# the heaviest field in the game (fship_2) has 12 pages.
FIELD_BG_PAGE_PX_CHOICES = [
    # Off is now its OWN value rather than a synonym for 256. It had to be:
    # build.py returned early on `px == VANILLA_PX`, so selecting "256"
    # disabled the entire pass and "256px truecolor" -- the cheapest
    # promotion available -- could not be requested at all.
    (0, 'Off \u2014 as shipped (8-bit paletted pages)'),
    # Cheaper than the paletted pages the game already ships, because a
    # 128px truecolor page is 0.09 MB against their 0.31. Halves background
    # resolution, so it is the setting to pick when memory is the problem
    # rather than sharpness -- the worst field in the game lands at 1.13 MB.
    (128, '128px \u2014 lowest memory: 0.09 MB/page, worst field 1.12 MB '
          '(softer than vanilla)'),
    # THE ROW THAT HAD NEVER BEEN TRIED, because the UI could not express it.
    # Same resolution as vanilla, but truecolor: no palette banding, no
    # colour-key restriction, no neighbouring-palette substitution. 21% more
    # than vanilla paletted, and every page on every field fits, so partial
    # promotion disappears entirely.
    # 256 IS NOT A QUALITY WIN, and the measurements say so:
    #   * the downscale is correct (Pillow premultiplies alpha on RGBA
    #     resize -- checked, no fringing), and
    #   * R5G6B5 is 20x more accurate than the optimised 256-colour palette
    #     it replaces (RMSE 0.20 against 4.28 on a real page),
    # so nothing is broken. But at 256 the mod's art is DOWNSCALED BACK to
    # vanilla resolution, which is a round trip through the upscaler: it
    # cannot be sharper than the vanilla page and will usually be slightly
    # softer. The only gain is colour depth. If it looked worse, this is why,
    # and the answer is to go UP the ladder, not down.
    (256, '256px \u2014 vanilla resolution, truecolor only: 0.38 MB/page, '
          'worst field 4.50 MB (no sharpness gain)'),
    # THE MIDDLE OF THE LADDER, reachable because the loader's read count is
    # PADDED to the nearest encodable immediate rather than being exact --
    # see ff7nx_fieldbg.read_bytes(). 2-16% storage waste on a term that is
    # only a third of the cost.
    (320, '320px \u2014 1.2x vanilla detail: 0.61 MB/page, worst field 7.31 MB'),
    (384, '384px \u2014 1.5x vanilla detail: 0.88 MB/page, worst field 10.50 MB'),
    (448, '448px \u2014 1.8x vanilla detail: 1.20 MB/page, worst field 14.44 MB'),
    # 12 pages x 1.50 MB = 18.00 MB, which is exactly where black bars were
    # measured. That is not proof the two are the same thing -- failure was
    # non-monotonic -- but it is why this is no longer the default.
    (512, '512px \u2014 2x sharper: 1.50 MB/page, worst field 18.00 MB '
          '(black bars were measured at 18 MB)'),
    (768, '768px \u2014 3x sharper: 3.38 MB/page, worst field 40.50 MB'),
    (1024, '1024px \u2014 4x sharper: 6.00 MB/page, worst field 72.00 MB'),
]

# How much field-background texture ONE field may ask for. This is the setting
# that decides how many of a field's pages can be upscaled at all, so it is the
# other half of the page-size choice above and belongs next to it.
#
# It is not a quality slider with a safety factor -- the numbers came off
# hardware. field_load_textures (x86 0x640292) aborts its whole loop on the
# FIRST allocation failure, so every page after that one keeps handle 0 and
# never draws. That is what the scattered black squares were. MEASURED:
#
#     elmin1_1 .. elmin2_2   CLEAN   1 x 512 page   2.44 MB
#     nmkin_1                BLACK   3 x 512 pages  6.06 MB
#     nmkin_2 .. mds5_1      BLACK   4 x 512 pages  7.88-8.50 MB
#
# So the real ceiling is somewhere in [2.44, 6.06). Anything at or above 6.0
# is within touching distance of a field that was measured black, which is why
# the label says so rather than leaving it to be found out.
# The measured bracket is [2.44, 6.06) MB and everything above it is
# UNTESTED, not known-bad -- 6.06 was one field, on one build, at 512px
# pages. It is also the wrong knob to reason about alone now that the page
# SIZE is selectable: one 512px truecolor page costs 1.50 MB but one 768px
# page costs 3.54 MB, so 4.0 MB fits three of the former and exactly ONE of
# the latter. A budget that is conservative at 512 is starvation at 768.
#
# The high entries exist so that can actually be tested rather than argued
# about. They are labelled for what they are.
# UNLIMITED IS THE DEFAULT NOW, and the reason is that the budget's own data
# refuted the model behind it:
#
#   * failure is not monotonic -- hardware gave black bars at 18 MB and a
#     clean picture at 14. No "runs out of memory" ceiling does that.
#   * lowering it to 4.0 MB made the margins WORSE.
#   * it only ever produced PARTIAL promotion (984 of 4,488 pages in the last
#     build), which is what puts a truecolor page beside a paletted one in the
#     same picture.
#
# The page SIZE is the real control: at 256px every page on every field fits
# with the worst field at 4.50 MB, so there is nothing left for a cap to
# protect. The numeric entries stay for bisecting a suspected ceiling.
# Megabytes of RUNTIME truecolor cost per field (6*px^2 a page). The labels
# name what each buys at the two sizes that matter, because the number means
# a different picture at each: 4.5 MB is twelve pages at 256px and three at
# 512px, and that is the whole reason this is in bytes and not pages.
FIELD_BG_BUDGET_CHOICES = [
    (0.0,  'Unlimited \u2014 right at 256px, NOT at 512px'),
    (3.0,  '3.0 MB \u2014  2 pages at 512px,  8 at 256px (safest at 512)'),
    (4.5,  '4.5 MB \u2014  3 pages at 512px, 12 at 256px'),
    (4.0,  '4.0 MB \u2014 conservative (only ONE 768px page fits)'),
    (5.0,  '5.0 MB'),
    (5.5,  '5.5 MB \u2014 the old default'),
    (5.75, '5.75 MB \u2014 a little more headroom'),
    (6.0,  '6.0 MB \u2014 6.06 MB was measured black once, at 512px'),
    (7.5,  '7.5 MB \u2014 two 768px pages'),
    (9.0,  '9.0 MB'),
    (11.0, '11.0 MB \u2014 three 768px pages'),
    (14.0, '14.0 MB \u2014 clean on hardware, at 512px'),
    (18.0, '18.0 MB \u2014 black bars were measured here, at 512px'),
]

# Whether a field promotes EVERY page it can or none at all.
#
# All-or-nothing is the default and is the setting that buys CONSISTENCY.
# "Promote the busiest N pages until the budget runs out" optimises for
# covering one screen and against the picture looking like a single thing --
# it is the direct cause of a truecolor page sitting next to a paletted one.
# A field that cannot be done completely keeps its Switch-vanilla background,
# which is correct, just not upgraded.
# TRUECOLOR PAGES PER FIELD.
#
# The value IS `field_bg_dense.MAX_TRUECOLOR_PAGES` -- how many pages of a
# field the dense repack may promote from 8-bit paletted to 16-bit.
#
# THIS CONTROL USED TO BE "field background promotion" AND IT DID NOTHING.
# It set `field_bg_repack.all_or_nothing()`, and `field_bg_repack.upgrade()`
# stopped being called when `field_bg_dense.dense_repack()` replaced it --
# which never reads the setting. MEASURED: zero references in field_bg_dense,
# and build.py's `st` (which carried `pages_allornothing`) is set to None and
# never reassigned, so the report could not fire either. Both values produced
# byte-identical builds while the log printed "PARTIAL promotion:" as though
# something had happened. FINDINGS-110 \u00a75.
#
# The slot is reused rather than added because a dead control that prints a
# claim about a code path that no longer runs is worse than no control.
#
# 3 is what shipped. It was NOT measured as a ceiling -- field_bg_dense's own
# comment says it is descriptive ("the working promotion never put more than
# THREE truecolor pages in one field, mean 1.41"), and the numbers behind it
# came from 512px experiments whose model `budget_bytes()` already retracts.
# The thing it was really guarding against -- field_load_textures giving up
# part-way and leaving pages on handle 0 -- turned out to be the 256-tiles-
# per-page overrun, which `field_bg_pagecap` now prevents outright.
# 3 IS A HARD CEILING NOW, NOT A TUNING CHOICE. The opaque truecolor band is
# slots 26, 27, 28 -- see field_bg_native.D2_OPAQUE_SLOTS for the measurement
# (every depth-2 page in the entire vanilla archive is in one of those three,
# and every build of ours that used slot 29+ produced black squares). Values
# above 3 are not offered because the repack cannot honour them.
FIELD_BG_TRUECOLOR_CHOICES = [
    (3, '3 pages per field \u2014 the opaque band is 3 slots wide'),
    (0, 'Off \u2014 no truecolor promotion at all'),
    (2, '2 pages per field'),
    (1, '1 page per field'),
]

# THE SETTING THAT BUYS PAGE SIZE WITHOUT BLACK SQUARES.
#
# Normally promoting a page ADDS one: the promoted cells go to a new
# truecolor page, but the original paletted page must stay alive for every
# tile that could not move -- colour-key cells, fx-page tiles, cells the mod
# draws transparent. Two textures where there was one, and every texture is
# an allocation field_load_textures can fail on.
#
# A page whose tiles ALL move is different: nothing references it afterwards,
# it gets freed, and the page count does not grow. Promoting only those holds
# the texture count at vanilla's, which is what lets the page SIZE go up.
#
# ...and it costs almost the whole feature to get it: only ~13% of pages have
# every tile movable, so replace-only promotes 341 pages where the mod covers
# 2,299. The build looks very close to Off.
#
# NO-GROWTH gives the identical promise for 2.4x the art, because it attacks
# the other side of the equation. The reason promotion adds a page is that the
# original stays alive for the tiles that could not move -- but that original
# is then mostly EMPTY, and a page costs one texture whether it holds 256
# cells or 6. So: promote freely, pack the leftovers back down
# (field_bg_compact), measure what the field actually ended with, and if it is
# still bigger than what it started with, repack at a lower ceiling until it
# is not. The guarantee is checked on the real output rather than argued from
# a rule about pages.
#
# MEASURED over all 709 fields at 256px, ceiling 12, all-or-nothing:
#
#                              pages promoted   tiles      mean pages   grew
#     Off (ceiling 12 only)         2,299       350,413       5.48       438
#     No growth                       820       120,452       4.74         0
#     Replace only                    341        81,024       4.72         0
#
# COSMOS'S WIDESCREEN ART, WRITTEN INTO THE PAGE THAT IS ALREADY THERE.
#
# The coloured side bands are MISSING TEXTURES. Cosmos's `chunk.9` places
# margin tiles pointing at cells that are BLANK in the vanilla page and
# PAINTED in its .dds upscale; FFNx loads the .dds and draws scenery, this
# port skips it and the tile samples the blank placeholder, so the whole band
# comes out one flat colour. MEASURED over 45 fields: of 3,072 such tiles,
# 2,501 (81%) have real art in the .dds.
#
# Promotion to truecolor is the only OTHER way to get that art on screen, and
# three things forbid it for most pages -- a colour key, an fx page sharing
# one u,v, transparency. This writes INDICES into the existing paletted page
# instead, which dodges all three: index 0 stays index 0, the page keeps its
# identity, the format does not change. No new page, no new texture, no VRAM.
#
# Needs Cosmos Limit Break (or any mod shipping field .dds) to be enabled --
# with no art to read it does nothing and says so in the log.
FIELD_BG_MARGIN_ART_CHOICES = [
    (2, 'Margin + interior \u2014 recommended: use the mod\u2019s art '
        'everywhere it fits'),
    (1, 'Margin only \u2014 fill the 16:9 side bands, leave the 4:3 picture'),
    (0, 'Off \u2014 leave the margin as the flat placeholder colour'),
]


FIELD_BG_REPLACE_ONLY_CHOICES = [
    (2, 'No growth \u2014 recommended: promote, then compact to pay for it'),
    (0, 'Off \u2014 promote any page the mod covers (adds pages)'),
    (1, 'Replace only \u2014 strictest, promotes ~13% of pages'),
]

# ONE SETTING THAT DRIVES THE OTHER FIVE.
#
# The five controls below grew one at a time as each theory was tested, and
# together they are unusable: page size, page growth, max pages, promotion
# and budget all interact, and only one combination of them is a guarantee
# rather than a bet. This collapses them to a single choice.
#
# Every preset pins the four safety knobs to the same values -- replace-only
# ON, ceiling 12, all-or-nothing, budget unlimited -- because that is the
# combination that cannot ask the loader for more textures than the stock
# game does. The preset chooses ONLY the page size, which is the one axis
# that is a real trade rather than a guess.
#
# (name, page_px, replace_only, max_pages, partial, budget_mb)
# MEASURED, and it decides which of these is worth using: a page can only be
# promoted if NO tile on it draws from an fx page and NO pixel is a colour
# key. Across a real flevel that is
#
#     437 of 3,315 paletted pages  (13%)
#     one page or more in 225 of 695 fields  (32%)
#     12% of a field's pages, on average
#
# So ABOVE 256 the picture is 13% sharp pages beside 87% unchanged ones, in
# the same frame. That is not a tuning failure, it is what the setting does,
# and it is why 448px looked worse than leaving it off. The two entries below
# that are marked MIXED are honest about it rather than describing megabytes.
#
# At 256 there is no mismatch to have -- promoted and unpromoted pages are
# the same size and differ only in colour depth.
#
# "REPLACE-ONLY COSTS NOTHING VISUALLY AT 256" -- FALSIFIED ON HARDWARE.
#
# That claim stood here for several revisions and it is wrong, in a way the
# table above already implied and nobody read: replace-only is not neutral
# with respect to WHERE on the screen the upgrade lands.
#
# Replace-only promotes a page only if EVERY tile on it can move. Cosmos's
# extended widescreen art sits on pages the mod itself added -- every tile on
# them is the mod's, none is an fx-page sharer or a colour-key cell -- so
# those pages ALWAYS qualify. The vanilla pages that cover the middle of the
# screen carry ~48,000 tiles that structurally cannot move (24,864 share a
# u,v with an animation frame; 23,174 have a colour key a truecolor page
# cannot hold), and ONE such tile disqualifies the whole page, so those
# pages almost NEVER qualify.
#
# The result, reported from a console in 2026-08: upscaled art in the 16:9
# side regions and vanilla art in the 4:3 centre, in the same frame. That is
# exactly the mismatch replace-only was supposed to prevent, and it is
# replace-only that produces it.
#
# MEASURED, same build: 692 fields had the mod's art available and 113 got
# any of it. Sixteen per cent.
#
# So the presets pin NO GROWTH (2), not replace-only (1). Both promise the
# loader is never asked for more textures than the archive already asked it
# for -- no-growth gets there by promoting freely and then compacting to pay
# for it, rather than by refusing 87% of pages up front. See
# field_bg_repack.no_growth().
FIELD_BG_GROWTH_AT_256 = 2
FIELD_BG_REPLACE_ONLY_AT_256 = FIELD_BG_GROWTH_AT_256   # old name, kept
FIELD_BG_PRESET_CHOICES = [
    (0, 'Off — as shipped (recommended for now)'),
    (1, 'Uniform — 256px: same size everywhere, colour depth only'),
    (2, 'Sharper but MIXED — 384px on ~13% of pages, 256px on the rest'),
    (3, 'Sharpest but MIXED — 512px on ~13% of pages, 256px on the rest'),
    (9, 'Custom — use the advanced settings below'),
]

FIELD_BG_PRESETS = {
    #        px   replace_only  max_pages  truecolor  budget
    #
    # The fourth column used to be the dead `partial` flag and was 0 in every
    # row, which is why swapping the control for a real one needed a real
    # value here. 3 is what the build has always produced.
    0:   (0,      0,            12,        3,         0.0),
    1:   (256,    2,            12,        3,         0.0),
    2:   (384,    2,            12,        3,         0.0),
    3:   (512,    2,            12,        3,         0.0),
}


# THE CEILING THAT ACTUALLY BINDS. Not bytes -- TEXTURES.
#
# field_load_textures (x86 0x640292) makes one texture per present page and
# abandons the whole loop on the first allocation it cannot serve; every page
# after that keeps handle 0 and draws nothing. That is what scattered black
# squares are, and it is driven by the NUMBER of pages.
#
# The repack ADDS pages rather than replacing them, because a page can only
# be freed once nothing points at it and huge numbers of tiles keep their
# paletted page (colour-key cells, fx-page tiles). MEASURED on a real build:
# 1,697 new pages against 184 freed, +2.3 per field, and gaiin_4 went from 10
# pages to 17 -- more than any field the stock game ever ships.
#
# 12 is vanilla's heaviest field (fship_2) and only 5 fields reach it, so it
# is the order the port was provisioned for. Raise it to find the real
# ceiling; the build log names every field that exceeds whatever is set here.
FIELD_BG_MAX_PAGES_CHOICES = [
    (12, '12 pages \u2014 recommended (vanilla\u2019s heaviest field)'),
    (10, '10 pages \u2014 conservative'),
    (14, '14 pages'),
    (16, '16 pages \u2014 the depth-2 slot limit'),
    (20, '20 pages'),
    (0,  'Unlimited \u2014 no ceiling (black squares were MEASURED here)'),
]

# What the game's busy-wait frame limiters aim for. The display paces the game
# at 60 either way; this only decides how much room is left between the limiter
# releasing and the vsync deadline, and running out of that room is what costs
# a whole refresh. See ff7nx_60fps.frame_pacing_note().
LIMITER_FPS_CHOICES = [
    (0,   'Off \u2014 aim for 60 (as shipped)'),
    (61,  'Aim for 61'),
    (62,  'Aim for 62'),
    (63,  'Aim for 63'),
    (64,  'Aim for 64'),
    (65,  'Aim for 65'),
    (66,  'Aim for 66'),
    (68,  'Aim for 68'),
    (70,  'Aim for 70'),
    (240, 'Diagnostic only \u2014 240 (runs the game fast)'),
]

# Picture quality for converted FMVs. Sizes in the labels come from
# movies.py's measurements on a 16-second reference clip, so the cost of each
# choice is visible before a long build rather than after it. True lossless is
# absent on purpose: it requires H.264's High 4:4:4 Predictive profile, which
# the console cannot decode. Frame rate is not a choice -- a movie is built at
# whatever rate its mod ships.
MOVIE_QUALITY_CHOICES = [
    (name, 'Movie quality: ' + label)
    for name, _crf, label in build.movie_convert.QUALITY_LEVELS
]

# How a converted FMV is SIZED. Not the same dial as quality: quality is the
# crf, this is the pixel count. The console draws a movie into at most
# 1440x1080 device pixels (measured out of exefs/main -- see movies.py), and
# romfs/shaders/video_p.glsl samples it with a single bilinear tap, so a
# larger file is minified by four texels per pixel no matter how many texels
# that pixel really covers. Resampling on the PC with Lanczos instead is
# strictly better and makes the files smaller.
MOVIE_FIT_CHOICES = [
    (name, 'Movie size: ' + label)
    for name, label in build.movie_convert.FIT_CHOICES
]

# The port's movie shader hardcodes the BT.709 limited-range matrix. FF7's
# FMVs are standard definition, so an upscale pack made from them is BT.601
# or untagged, and decodes through the wrong matrix -- greys stay exact and
# saturated colour drifts by up to 39/255, which reads as weak colour rather
# than as a bug.
MOVIE_COLOUR_CHOICES = [
    (name, 'Movie colour: ' + label)
    for name, label in build.movie_convert.COLOUR_CHOICES
]

# 16:9.
#
# There is ONE supported value and it is `ws` -- ff7nx_ws.py, the pipeline
# built the way FFNx actually does it: per-field, out of the mod's own
# `CONFIG/widescreen/config.toml`, baked into flevel.lgp at build time.
#
# The three values that used to be here are gone from this list on purpose,
# and the reason is worth keeping written down:
#
#   field    Was labelled "16:9 -- recommended". It is the v7 build that
#            README-widescreen-v8 records as a hardware REGRESSION: bars
#            still present, character misaligned with the background, and
#            the walkmesh no longer matching what is drawn. The obvious
#            option was the broken one.
#   stretch  KNOWN BAD. Widens the render target and nothing else, so the
#            same 4:3 picture is painted across it. Confirmed on hardware:
#            everything stretches, battles and the start screen included.
#   fit      KNOWN BAD. stretch, plus a cave on gfx_drv_setviewport that
#            rewrites its arguments. Confirmed on hardware: bars still
#            present, menus still stretched, and background-pinned objects
#            DRIFT.
#
# All three still work if their value is put in SEVENTH_NX_WIDESCREEN by
# hand -- ff7nx_widescreen.py and ff7nx_field169.py are untouched -- so a
# deliberate diagnostic run is still one environment variable away. What is
# gone is the ability to select a known-bad build by clicking the entry that
# looks like the right one.
#
# What `ws` does today, stated plainly because the label cannot:
#
#   * It bakes FFNx's per-field camera ranges into flevel.lgp section 8.
#     With Cosmos Limit Break's config that is the difference between 341
#     and 647 of 711 fields being eligible for widescreen at all.
#   * It emits the per-field wide/not-wide table the module patch needs.
#   * It does NOT open exefs/main. The picture stays 4:3 until the framing
#     stage lands, which is why the label says "content".
#
# The framing stage is deliberately not a dropdown entry. A drawn control
# is a recommendation, and four hardware builds have been spent learning
# that this one is not ready to be recommended. It is reachable by
# SEVENTH_NX_WS_FRAMING=1 for a measurement run. See README-46.
# The labels used to call the DATA-ONLY entry "16:9 -- recommended" and the
# real one "+ wide frame". That was true when the framing stage was unproven;
# it is not any more, and it sent a build to the wrong stage: `ws` bakes
# camera ranges and never opens exefs/main, so the console still shows 4:3
# and nothing in the log said why. The label now says what each one DOES.
# Values are unchanged, so a saved settings.json still selects the same
# build as before.
WIDESCREEN_CHOICES = [
    ('', 'Off \u2014 4:3 with black bars'),
    ('ws', 'Data only \u2014 camera ranges; picture stays 4:3'),
    ('ws-3d', '16:9 widescreen \u2014 recommended'),
]
WIDESCREEN_LABELS = dict(WIDESCREEN_CHOICES)
WIDESCREEN_BY_LABEL = {v: k for k, v in WIDESCREEN_CHOICES}

# The field render target.
#
# HANDOFF-51: the port draws the field into a hardcoded 320x240 offscreen so
# the pre-rendered background lands 1:1, one source texel on one buffer
# pixel, and reconstructs it with the 2xSaI/HQ4x pair on the way to the
# screen. Widescreen squeezed 853 game units through those same 320 pixels,
# which turned a 16-texel tile into 12 pixels -- a minification whose
# sampling phase repeats every 3 buffer pixels and shows up as 12-pixel
# vertical bands glued to the screen. Widening the buffer restores the 1:1.
#
# The numbers are not free choices. With buffer W x H and shader scale S the
# field gets W*S/640 pixels per game unit across and H/480 down, and whole
# pixels per texel needs both to be n/2 -- so H = 240n, S = 320n/W, and the
# visible span is 2W/n units. W then picks the aspect ratio on its own.
# Entries below are the values of W that are even (so game x=0 lands on a
# whole pixel) and closest to 16:9.
#
# 1x is what hardware has confirmed. 3x is the arithmetically exact one and
# is a genuinely different look, not simply a better one -- at 3x the
# hardware sampler magnifies the background before the 2xSaI/HQ4x kernel
# sees it, so the kernel has much less to do.
FIELD_BUF_CHOICES = [
    (0, 'Off \u2014 320\u00d7240 (has the vertical bands in 16:9)'),
    (1, '1\u00d7 \u2014 428\u00d7240, no bands \u2014 recommended '
        '(tested on hardware)'),
    (2, '2\u00d7 \u2014 854\u00d7480, no bands, sharper field'),
    (3, '3\u00d7 \u2014 1280\u00d7720, no bands, exactly 16:9, field at '
        'native 720p'),
]
FIELD_BUF_LABELS = dict(FIELD_BUF_CHOICES)
FIELD_BUF_BY_LABEL = {v: k for k, v in FIELD_BUF_CHOICES}

# The background scaler and the full-screen AA. Both are PIXEL shaders the
# port already loads from romfs; these are drop-in replacements that used to
# have to be copied onto the card by hand. See ff7nx_shaders.py.
SCALER_CHOICES = [
    ('', 'Stock \u2014 the port\u2019s own 2xSaI / HQ4x'),
    ('hd', 'HD \u2014 Catmull-Rom + sharpen (recommended)'),
    ('xbr', 'xBR \u2014 edge-directed, crisp flats'),
    ('crisp', 'Crisp \u2014 nearest neighbour (no reconstruction)'),
    ('soft', 'Soft \u2014 plain bilinear (a control, not an upgrade)'),
]
SCALER_LABELS = dict(SCALER_CHOICES)
SCALER_BY_LABEL = {v: k for k, v in SCALER_CHOICES}

FXAA_CHOICES = [
    ('', 'Stock \u2014 the port\u2019s own FXAA'),
    ('hd', 'HD FXAA \u2014 retuned'),
    ('off', 'Off \u2014 sharper, cheaper, jaggier model edges'),
]
FXAA_LABELS = dict(FXAA_CHOICES)
FXAA_BY_LABEL = {v: k for k, v in FXAA_CHOICES}

VIDEO_SHADER_CHOICES = [
    ('', 'Stock \u2014 the port\u2019s own movie shader'),
    ('hd', 'HD \u2014 custom_shaders/hd_video'),
]
VIDEO_SHADER_LABELS = dict(VIDEO_SHADER_CHOICES)
VIDEO_SHADER_BY_LABEL = {v: k for k, v in VIDEO_SHADER_CHOICES}

MOD_LOAD_ORDER = {
    'miscellaneous': 0,
    'animations': 1,
    'battle models': 2,
    'battle textures': 3,
    'field models': 4,
    'field textures': 5,
    'gameplay': 6,
    'media': 7,
    'minigames': 8,
    'spell textures': 9,
    'user interface': 10,
    'world models': 11,
    'world textures': 12,
    'shaders': 13,
    'unknown': 14,
}
UNKNOWN_ORDER = MOD_LOAD_ORDER['unknown']


def mod_category(mod):
    """Category declared in mod.xml, or '' when unavailable."""
    return ((getattr(mod.manifest, 'category', '') or '').strip()
            if mod.manifest else '')


def mod_load_rank(mod):
    """Lower rank = higher priority, exactly as 7th Heaven sorts."""
    return MOD_LOAD_ORDER.get(mod_category(mod).lower(), UNKNOWN_ORDER)


def discover_mods():
    """Mods in 7th Heaven priority order: highest priority first."""
    if not os.path.isdir(MODS_DIR):
        return []
    found = []
    for fn in sorted(os.listdir(MODS_DIR)):
        if fn.lower().endswith('.iro'):
            mod = build.Mod(os.path.join(MODS_DIR, fn), CACHE_DIR)
            # Cheap: reads mod.xml only, no extraction. Needed up front so
            # the category is known before we sort.
            mod._load_manifest()
            found.append(mod)
    found.sort(key=lambda m: (mod_load_rank(m), m.display_name.lower()))
    return found


def run_build(mods, enabled, settings_by_mod, log, progress,
              fps_60=False):
    # COUNTER GUARD (HANDOFF-121 3.6). Tee the log, and at the end name every
    # counter that moved outside the pass this build was meant to change. It
    # only ever writes extra lines -- no build is ever stopped by it -- and it
    # is wrapped in try/except at both ends because a diagnostic must not be
    # able to fail a 20-minute build.
    _guard = None
    try:
        # expect defaults to build_guard.EXPECTED_MOVEMENT, which is declared
        # per build. Do not pass a set here -- that is how build 36 ended up
        # told to expect page-cap movement in a logging-only change.
        _guard = build_guard.CounterGuard(
            log,
            label='window cap %s, fx split %s' % (
                build.field_bg_pagecap.WINDOW_HARD_CAP,
                build.field_bg_pagecap.FX_SPLIT))
        log = _guard.log
    except Exception:                                           # noqa: BLE001
        _guard = None

    def _finish(result):
        if _guard is not None:
            try:
                _guard.finish()
            except Exception:                                   # noqa: BLE001
                pass
        return result

    if DUMP is None or not os.path.isdir(WORKINGDIR):
        log('ERROR: no game dump found.')
        log(f'Put your Switch dump at {os.path.join(HERE, "dump")} so that')
        log(f'  {os.path.join(HERE, "dump", build.DUMP_WORKINGDIR)} exists,')
        log(f'or set {build.DUMP_ENV} to point at it.')
        return False

    log(f'{DUMP.describe()}: {DUMP.root}')
    if DUMP.kind == 'dump':
        for label, path in (('workingdir', DUMP.workingdir),
                            ('exe       ', DUMP.exe),
                            ('exefs/main', DUMP.nso),
                            ('shaders   ', DUMP.shaders)):
            log(f'    {label}  {path if path else "(not in this dump)"}')
    log('reading your archives ...')
    catalogs, paths = build.load_catalogs(WORKINGDIR, log)
    if not catalogs:
        log(f'ERROR: no LGP archives found under {WORKINGDIR}')
        log('Expected e.g. .../workingdir/data/field/char.lgp')
        return False

    active = [m for m in mods if enabled.get(m.filename)]
    if not active:
        log('nothing enabled.')
        return False

    log('')
    for mod in active:
        mod.ensure_extracted(log, lambda i, n: progress(i, n, 'extracting'))

    log('')
    log('mod priority (7th Heaven category order, first listed wins):')
    for mod in active:
        log(f'   {mod_load_rank(mod):>2}  '
            f'{(mod_category(mod) or "Unknown"):<16} {mod.display_name}')

    # `active` is in 7H priority order (highest first); build_plan applies
    # later-wins, so reverse it to make the highest-priority mod win.
    log('')
    # The vanilla track list, so a file under `vgmstream/` can be told apart
    # from an FFNx alias. Missing dump -> None -> take them all, which is the
    # right default for a soundtrack mod.
    music_names = None
    if DUMP is not None and DUMP.workingdir:
        mdir = os.path.join(DUMP.workingdir, 'data', 'music_ogg')
        if os.path.isdir(mdir):
            music_names = {n.lower() for n in os.listdir(mdir)
                           if n.lower().endswith('.ogg')}
    # `<Conditional>` RuntimeVar gates. The variables these mods test are
    # bytes in the ff7 exe -- the flag block other mods write to advertise
    # themselves -- so the exe we are about to ship answers them the same way
    # 7th Heaven's live memory read would.
    runtime_read = None
    if DUMP is not None and DUMP.exe and os.path.exists(DUMP.exe):
        try:
            import exe_patch
            exe_bytes = open(DUMP.exe, 'rb').read()
            pe = exe_patch.parse_pe(exe_bytes)
            runtime_read = iro.exe_var_reader(
                exe_bytes, lambda va: exe_patch.va_to_offset(pe, va))
        except Exception as exc:                               # noqa: BLE001
            log(f'note: RuntimeVar gates not evaluated ({exc}); every '
                'conditional folder is kept')
    plan = build.build_plan(list(reversed(active)),
                            settings_by_mod, catalogs, log,
                            music_names=music_names,
                            runtime_read=runtime_read)

    log('')
    log(f'portable files : {plan.total_portable()}')
    if plan.skipped_ffnx:
        log(f'FFNx textures  : {plan.skipped_ffnx} (skipped, no Switch loader)')
    if plan.music:
        extra = (f' ({plan.music_from_vgmstream} from vgmstream/)'
                 if plan.music_from_vgmstream else '')
        log(f'music tracks   : {len(plan.music)}{extra}')
    if plan.skipped_ffnx_audio:
        detail = ', '.join(f'{d}/ {n}' for d, n in
                           sorted(plan.skipped_ffnx_audio.items()))
        total = sum(plan.skipped_ffnx_audio.values())
        log(f'FFNx audio     : {total} (skipped, no Switch loader) -- {detail}')
        log('                 the Switch port reads .ogg only from '
            'data/music_ogg; it has no external SFX, ambience or voice '
            'layer, so these would be dead files on the SD card.')
        if music_names is not None and 'vgmstream' in plan.skipped_ffnx_audio:
            log('                 vgmstream/ entries were skipped only where '
                'the name is not a real track;')
            log('                 the rest were taken as music.')
    if plan.unmatched:
        log(f'unrecognised   : {len(plan.unmatched)}')
        for f in plan.unmatched[:5]:
            log(f'    {f}')
    for archive, name, first, second in plan.conflicts[:10]:
        log(f'override: {archive}/{name}  {first} -> {second}')
    if plan.total_portable() == 0:
        log('')
        log('Nothing to do -- these mods are FFNx-only.')
        return False

    # Field background page size needs BOTH halves -- six words in
    # exefs/main and a rewritten flevel.lgp -- and _build_flevel runs inside
    # apply_plan, before the module is touched. Without a dump there is no
    # module, so turn the whole feature off HERE rather than let apply_plan
    # write an flevel the module cannot read.
    # 256px is exempt: it writes none of the six size words, so it needs no
    # module at all. Only the SIZE settings do. (A build big enough to need
    # the wider decompression buffer is caught by apply_field_bg_pages
    # itself, which has FIELD_BG_MAX_RAW and can therefore tell.)
    if (build.ff7nx_fieldbg.enabled()
            and build.ff7nx_fieldbg.page_px()
            != build.ff7nx_fieldbg.VANILLA_PAGE_PX
            and (DUMP is None or not DUMP.nso)):
        log('! field background page size: needs exefs/main from a full '
            'game dump -- turned off for this build, backgrounds stay as '
            'shipped')
        os.environ[build.ff7nx_fieldbg.PAGE_PX_ENV] = str(
            build.ff7nx_fieldbg.OFF_PAGE_PX)

    log('')
    produced = build.apply_plan(plan, paths, SDOUT_DIR, log,
                               lambda i, n, name: progress(i, n, name),
                               dump=DUMP)
    # The 60 FPS patches go in LAST, into this same sdout. They read the
    # battle.lgp the mods were just written into (its animation-script waits
    # need scaling to match the 4x-longer ?da animations) and the exe that
    # HEXT may just have produced, so they cannot run any earlier.
    if fps_60:
        produced += build.apply_fps_patches(SDOUT_DIR, DUMP, log,
                                            produced)
    # And the pillarbox fix after THAT, for the same reason: it edits
    # exefs/main, so it has to build on whatever the 60 FPS set just wrote
    # rather than on the dump's copy. Gated on its own env var, so a build
    # with it off is byte-identical to before this existed.
    produced += build.apply_widescreen(SDOUT_DIR, DUMP, log, produced)
    # 16:9 field, last, for the third time for the same reason -- it edits
    # exefs/main too, and it edits flevel.lgp, so it has to see whatever the
    # passes above just wrote.
    #
    # These three are mutually exclusive by construction and none of them
    # can be selected from the dropdown any more:
    #   apply_widescreen     fires only for 'stretch' / 'fit'
    #   ff7nx_field169       fires only for 'field'
    #   ff7nx_ws.apply_module fires only for 'ws' AND SEVENTH_NX_WS_FRAMING
    # All three are reachable by environment variable for a diagnostic run.
    # The 16:9 the dropdown offers is the DATA half and lands inside
    # _build_flevel, well before any of this.
    produced += ff7nx_field169.apply(SDOUT_DIR, DUMP, log, produced)
    produced += build.ff7nx_ws.apply_module(SDOUT_DIR, DUMP, log, produced)
    # Field background page size, last of all, for the same reason again --
    # and note _build_flevel has ALREADY rewritten section 9 to match, so if
    # this pass cannot run the SD tree is inconsistent and says so.
    produced += build.apply_field_bg(SDOUT_DIR, DUMP, log, produced)
    # REMOVED: apply_bg_clear ("Black 16:9 margins") and apply_movie_clip
    # ("Clip models to the movie"). Both are retired on hardware results --
    # bg_clear changed nothing because the flat margin colour is not the clear
    # colour (ff7nx_marginart/ff7nx_marginpal fixed it at the source), and the
    # movie clip scissored the presentation blit and froze stale field art in
    # the margins. FINDINGS-92 §6. The modules stay on disk as derivations;
    # nothing calls them.
    #
    # LAST module pass, and it is now the only one that touches exefs/main
    # after ff7nx_ws: the 16:9 field frame -- no painted letterbox, the full
    # 480-unit window, the background and its sprites centred in it, the model
    # cull widened to the wider frame, the movie quad aligned to it, the FMV
    # margin bars, and the scripted-camera clamp. It goes last because it edits
    # exefs/main and because ff7nx_cave's allocator re-checks that its padding
    # holes are still zero IN THE MODULE IT IS HANDED, so it has to see
    # whatever every earlier cave actually took. Gated on the 16:9 setting; at
    # 4:3 it is a no-op by design. FINDINGS-88, FINDINGS-92.
    produced += build.apply_field_frame(SDOUT_DIR, DUMP, log, produced)
    # The FF7 guest heap, after every other module pass and on their output.
    # Nine words in the memory shims; it shares no site, no cave and no
    # padding hole with anything above, so it is last purely by the same rule
    # they all follow -- whoever edits exefs/main last must see what everyone
    # else wrote. This is the pass that lifts the ceiling on truecolor pages,
    # Cosmos art coverage and 512px backgrounds; ff7nx_heap.HEAP_MB is the
    # setting, and it is a CODE CONSTANT, not an environment variable.
    produced += build.apply_heap(SDOUT_DIR, DUMP, log, produced)
    # The custom PIXEL shader sets (background scaler, FXAA). These touch no
    # module at all, so they can go anywhere -- but they must go BEFORE
    # prune_stale, because that is what deletes them again when the setting
    # goes back to Stock. They also have to land after ff7nx_ws's vertex
    # shaders so both end up in the same romfs/ff7/shaders directory listing.
    produced += build.ff7nx_shaders.apply(SDOUT_DIR, log, produced)

    # Everything the build produces is known now, so anything a PREVIOUS
    # build left here that this one does not produce is stale -- a feature
    # toggled off since. Only ever removes files this packer itself wrote.
    build.prune_stale(SDOUT_DIR, produced, log)

    log('')
    if produced:
        log(f'done. {len(produced)} files in sdout/')
        log('copy the contents of sdout/ onto the root of your SD card.')
    else:
        log('nothing was written.')
    return _finish(bool(produced))


# ------------------------------------------------------------------- UI

def _reveal(path):
    """Open a folder in the OS file browser."""
    import subprocess
    if not os.path.isdir(path):
        return
    if sys.platform == 'darwin':
        subprocess.Popen(['open', path])
    elif sys.platform.startswith('win'):
        os.startfile(path)  # noqa
    else:
        subprocess.Popen(['xdg-open', path])


def launch_ui():
    import tkinter as tk
    from tkinter import ttk, messagebox
    from tkinter import font as tkfont

    mods = discover_mods()
    saved = build.load_settings(SETTINGS)
    global_saved = saved.get('__global__', {})
    cap_label_by_value = dict(FIELD_TEX_CAP_CHOICES)
    value_by_cap_label = {v: k for k, v in FIELD_TEX_CAP_CHOICES}
    initial_cap_value = global_saved.get('field_tex_cap', 0)
    if initial_cap_value not in cap_label_by_value:
        initial_cap_value = 0

    bg_cap_label_by_value = dict(BATTLE_BG_TEX_CAP_CHOICES)
    bg_value_by_cap_label = {v: k for k, v in BATTLE_BG_TEX_CAP_CHOICES}
    initial_bg_cap_value = global_saved.get('battle_bg_tex_cap', 256)
    if initial_bg_cap_value not in bg_cap_label_by_value:
        initial_bg_cap_value = 256

    fbg_label_by_value = dict(FIELD_BG_PAGE_PX_CHOICES)
    fbg_value_by_label = {v: k for k, v in FIELD_BG_PAGE_PX_CHOICES}
    # MIGRATION: 256 used to BE "off". A settings.json written before the
    # ladder was split says 256 and means "do nothing", so honour what it
    # meant rather than what it now says -- otherwise upgrading the tool
    # silently switches the field-background repack ON for everyone who had
    # it off, which is a much bigger change than they asked for.
    initial_fbg_value = global_saved.get('field_bg_page_px')
    if initial_fbg_value == 256 and not global_saved.get(
            'field_bg_ladder_v2'):
        initial_fbg_value = 0
    if initial_fbg_value not in fbg_label_by_value:
        initial_fbg_value = 0

    ftc_label_by_value = dict(FIELD_BG_TRUECOLOR_CHOICES)
    ftc_value_by_label = {v: k for k, v in FIELD_BG_TRUECOLOR_CHOICES}
    initial_ftc_value = global_saved.get(
        'field_bg_truecolor_pages', build.field_bg_dense.MAX_TRUECOLOR_PAGES)
    if initial_ftc_value not in ftc_label_by_value:
        initial_ftc_value = build.field_bg_dense.MAX_TRUECOLOR_PAGES

    fmaxp_label_by_value = dict(FIELD_BG_MAX_PAGES_CHOICES)
    fmaxp_value_by_label = {v: k for k, v in FIELD_BG_MAX_PAGES_CHOICES}
    _fmaxp_default = build.field_bg_repack.DEFAULT_MAX_TOTAL_PAGES
    initial_fmaxp_value = global_saved.get('field_bg_max_pages',
                                           _fmaxp_default)
    if initial_fmaxp_value not in fmaxp_label_by_value:
        initial_fmaxp_value = _fmaxp_default

    # Cheap (header-only, no decompression) so it's fine to do at startup.
    # Lets the "modifies:" annotations below be exact archive-name matches
    # instead of folder-name guesses.
    catalogs = {}
    if os.path.isdir(WORKINGDIR):
        try:
            catalogs, _ = build.load_catalogs(WORKINGDIR)
        except Exception:
            catalogs = {}

    root = tk.Tk()
    root.title('7th Heaven NX')
    root.geometry('1000x720')
    root.minsize(880, 600)

    # ---------- dark blue theme -----------------------------------------
    # This app always renders the same dark theme, regardless of the OS's
    # own light/dark setting. 'aqua' (macOS's native ttk theme) was the
    # source of the jarring white-selection-bar bug: aqua draws its own
    # native chrome per-widget and mostly ignores colors we set on it, so a
    # hardcoded "light blue" selection color rendered as a stark white/pale
    # bar with no way to fix it from here. 'clam' is fully style-driven --
    # nothing native, no OS-dependent surprises -- so every color below is
    # exactly what renders, on every platform, every time.
    BG_APP        = '#0b1220'   # window + all panel backgrounds
    BG_ROW        = '#151d30'   # unselected mod-row background
    BG_ROW_HOVER  = '#1a2438'   # mod-row background on mouse-over
    BG_ROW_SEL    = '#1c3a5e'   # selected mod-row background (blue tint)
    BORDER        = '#232e46'
    ACCENT        = '#4f9dff'
    ACCENT_SOFT   = '#8ec2ff'
    TEXT_PRIMARY  = '#e8ecf4'
    TEXT_SECONDARY = '#8b93a8'
    TEXT_MUTED    = '#5c6579'
    ARCHIVE_EXACT = ACCENT_SOFT
    ARCHIVE_EST   = TEXT_MUTED
    LOG_BG        = '#070b14'

    root.configure(bg=BG_APP)

    style = ttk.Style()
    try:
        style.theme_use('clam')
    except tk.TclError:
        pass

    style.configure('.', background=BG_APP, foreground=TEXT_PRIMARY,
                    fieldbackground=BG_ROW, bordercolor=BORDER,
                    darkcolor=BG_APP, lightcolor=BG_APP,
                    troughcolor=BG_ROW, focuscolor=ACCENT,
                    font=('Helvetica', 11))
    style.configure('TFrame', background=BG_APP)
    style.configure('TLabel', background=BG_APP, foreground=TEXT_PRIMARY)
    style.configure('TLabelframe', background=BG_APP, bordercolor=BORDER,
                    relief='solid', borderwidth=1)
    style.configure('TLabelframe.Label', background=BG_APP,
                    foreground=TEXT_SECONDARY, font=('Helvetica', 10, 'bold'))
    style.configure('TPanedwindow', background=BG_APP)
    style.configure('TSeparator', background=BORDER)

    style.configure('Header.TLabel', font=('Helvetica', 18, 'bold'),
                    foreground=TEXT_PRIMARY)
    style.configure('Sub.TLabel', foreground=TEXT_SECONDARY)
    style.configure('ModName.TLabel', font=('Helvetica', 12),
                    background=BG_ROW, foreground=TEXT_PRIMARY)
    style.configure('ModNameSelected.TLabel', font=('Helvetica', 12, 'bold'),
                    background=BG_ROW_SEL, foreground=ACCENT_SOFT)
    style.configure('ColHeader.TLabel', font=('Helvetica', 9, 'bold'),
                    foreground=TEXT_MUTED)
    style.configure('Archive.TLabel', font=('Menlo', 10),
                    foreground=ARCHIVE_EXACT)
    style.configure('ArchiveEst.TLabel', font=('Menlo', 10),
                    foreground=ARCHIVE_EST)

    style.configure('TButton', background=BG_ROW, foreground=TEXT_PRIMARY,
                    bordercolor=BORDER, padding=(10, 6))
    style.map('TButton',
              background=[('active', BG_ROW_HOVER), ('disabled', BG_APP)],
              foreground=[('disabled', TEXT_MUTED)])
    style.configure('Build.TButton', font=('Helvetica', 13, 'bold'),
                    padding=(18, 9), background=ACCENT, foreground='#08111e',
                    bordercolor=ACCENT)
    style.map('Build.TButton',
              background=[('active', ACCENT_SOFT), ('disabled', BG_ROW)],
              foreground=[('disabled', TEXT_MUTED)])

    style.configure('TCombobox', fieldbackground=BG_ROW, background=BG_ROW,
                    foreground=TEXT_PRIMARY, arrowcolor=TEXT_SECONDARY,
                    bordercolor=BORDER, selectbackground=BG_ROW,
                    selectforeground=TEXT_PRIMARY)
    style.map('TCombobox',
              fieldbackground=[('readonly', BG_ROW), ('focus', BG_ROW)],
              foreground=[('readonly', TEXT_PRIMARY)],
              background=[('readonly', BG_ROW)],
              bordercolor=[('focus', ACCENT)])
    # The Combobox's dropdown list is a separate, un-styled Tk Listbox --
    # without this it stays white-on-black regardless of the ttk style set
    # above, which would be its own jarring mismatch against a dark app.
    root.option_add('*TCombobox*Listbox.background', BG_ROW)
    root.option_add('*TCombobox*Listbox.foreground', TEXT_PRIMARY)
    root.option_add('*TCombobox*Listbox.selectBackground', ACCENT)
    root.option_add('*TCombobox*Listbox.selectForeground', '#08111e')
    root.option_add('*TCombobox*Listbox.font', ('Helvetica', 11))

    for pbstyle in ('TProgressbar', 'Horizontal.TProgressbar'):
        style.configure(pbstyle, background=ACCENT, troughcolor=BG_ROW,
                        bordercolor=BORDER, lightcolor=ACCENT,
                        darkcolor=ACCENT)

    style.configure('Vertical.TScrollbar', background=BORDER,
                    troughcolor=BG_APP, bordercolor=BG_APP,
                    arrowcolor=TEXT_SECONDARY, relief='flat', gripcount=0)
    style.map('Vertical.TScrollbar',
              background=[('active', ACCENT), ('pressed', ACCENT)])

    settings_by_mod = {}
    enabled = {}
    messages = queue.Queue()

    outer = ttk.Frame(root, padding=14)
    outer.pack(fill='both', expand=True)

    # ---------- header (top) ----------
    header = ttk.Frame(outer)
    header.pack(side='top', fill='x')

    # ---------- app icon ----------
    # Loaded TWICE, at two sizes, on purpose:
    #
    #   header  36 px, to sit level with the 18pt bold title
    #   window  256 px, for the taskbar / Dock / alt-tab
    #
    # The window icon must be BIG. Whoever draws it -- the Dock, the window
    # manager, the task switcher -- downscales it themselves, and handing them
    # the 36px header copy is what makes a Dock icon look like a thumbnail of
    # a thumbnail. icon.png is 500x500, so there is plenty to give.
    #
    # `_icon_keep` is not decoration: Tk holds only a WEAK reference to image
    # data, so a PhotoImage nothing else refers to is garbage collected and
    # the widget silently draws nothing. This is the most common way Tk icons
    # vanish and the list is what prevents it.
    ICON_PATH = os.path.join(HERE, 'icon.png')
    _icon_keep = []

    def _load_icon(px=None):
        """icon.png as a Tk image, resampled to `px` square (None = native)."""
        if not os.path.exists(ICON_PATH):
            return None
        img = None
        try:
            # preferred: a real resample
            from PIL import Image, ImageTk
            im = Image.open(ICON_PATH).convert('RGBA')
            if px and im.size != (px, px):
                im = im.resize((px, px), getattr(Image, 'LANCZOS',
                                                 Image.BICUBIC))
            img = ImageTk.PhotoImage(im)
        except Exception:
            # Pillow can be installed without ImageTk (separate package on
            # Linux), so fall back to Tk's own PNG reader. It can only
            # downscale by an integer factor, which is fine on a square source.
            try:
                raw = tk.PhotoImage(file=ICON_PATH)
                if px:
                    k = max(1, min(raw.width(), raw.height()) // px)
                    raw = raw.subsample(k, k) if k > 1 else raw
                img = raw
            except Exception:
                return None
        _icon_keep.append(img)
        return img

    # window / taskbar / Dock. `True` makes it the default for every toplevel,
    # so the Settings dialog inherits it too.
    _win_icon = _load_icon(256)
    if _win_icon is not None:
        try:
            root.iconphoto(True, _win_icon)
        except Exception:
            pass

    # macOS Dock. Tk's iconphoto does NOT reach the Dock for a script run --
    # the Dock shows the interpreter's own icon, because the process is
    # python3 rather than a bundled .app. AppKit can set it directly when
    # pyobjc happens to be installed; when it is not, this does nothing and
    # the Dock keeps showing Python. Making that reliable needs a real .app
    # bundle with an .icns, which is a packaging job, not a code one.
    if sys.platform == 'darwin':
        try:
            from AppKit import NSApplication, NSImage
            from Foundation import NSURL
            ns = NSImage.alloc().initWithContentsOfURL_(
                NSURL.fileURLWithPath_(ICON_PATH))
            if ns is not None:
                NSApplication.sharedApplication().setApplicationIconImage_(ns)
        except Exception:
            pass

    _icon = _load_icon(36)
    if _icon is not None:
        ttk.Label(header, image=_icon).pack(side='left', padx=(0, 10))

    ttk.Label(header, text='7th Heaven NX', style='Header.TLabel'
              ).pack(side='left')
    status = DUMP.describe() if DUMP else \
        'game dump MISSING — put your Switch dump in dump/'
    ttk.Label(header, text=f'v{VERSION}  ·  {len(mods)} mods  ·  {status}',
              style='Sub.TLabel').pack(side='left', padx=12)

    # ---------- settings (one button; the dialog is built on demand) -----
    # Everything below used to live loose in the header: four wide combo
    # boxes, three checkboxes and a fifth combo, all on one row. It did not
    # fit and nothing was grouped. The VARIABLES and the current_*() readers
    # are unchanged -- only where they are shown moved -- so every consumer
    # downstream (persistence, run_build, the env vars) behaves exactly as
    # before.
    #
    # Values, not widgets, carry the "needs a dump" rule. The old code
    # disabled the widget and set the var; the widgets now only exist while
    # the dialog is open, so the var is forced here and the dialog merely
    # greys the control to explain why.
    have_nso = bool(DUMP and DUMP.nso)

    cap_var = tk.StringVar(value=cap_label_by_value[initial_cap_value])

    def current_field_tex_cap():
        return value_by_cap_label.get(cap_var.get(), 0)

    bg_cap_var = tk.StringVar(value=bg_cap_label_by_value[initial_bg_cap_value])

    def current_battle_bg_tex_cap():
        return bg_value_by_cap_label.get(bg_cap_var.get(), 256)

    fbg_var = tk.StringVar(value=fbg_label_by_value[initial_fbg_value])

    def current_field_bg_page_px():
        return fbg_value_by_label.get(fbg_var.get(), 0)

    ftc_var = tk.StringVar(value=ftc_label_by_value[initial_ftc_value])

    def current_field_bg_truecolor():
        return ftc_value_by_label.get(
            ftc_var.get(), build.field_bg_dense.MAX_TRUECOLOR_PAGES)

    fmaxp_var = tk.StringVar(value=fmaxp_label_by_value[initial_fmaxp_value])

    def current_field_bg_max_pages():
        return fmaxp_value_by_label.get(fmaxp_var.get(), _fmaxp_default)

    fpre_label_by_value = dict(FIELD_BG_PRESET_CHOICES)
    fpre_value_by_label = {v: k for k, v in FIELD_BG_PRESET_CHOICES}

    frepl_label_by_value = dict(FIELD_BG_REPLACE_ONLY_CHOICES)
    frepl_value_by_label = {v: k for k, v in FIELD_BG_REPLACE_ONLY_CHOICES}
    initial_frepl_value = global_saved.get('field_bg_replace_only', 2)
    if initial_frepl_value not in frepl_label_by_value:
        initial_frepl_value = 0
    frepl_var = tk.StringVar(value=frepl_label_by_value[initial_frepl_value])

    def current_field_bg_replace_only():
        return frepl_value_by_label.get(frepl_var.get(), 0)

    fmart_label_by_value = dict(FIELD_BG_MARGIN_ART_CHOICES)
    fmart_value_by_label = {v: k for k, v in FIELD_BG_MARGIN_ART_CHOICES}
    initial_fmart_value = global_saved.get('margin_art', 0)
    if initial_fmart_value not in fmart_label_by_value:
        initial_fmart_value = 0
    fmart_var = tk.StringVar(value=fmart_label_by_value[initial_fmart_value])

    def current_field_bg_margin_art():
        return fmart_value_by_label.get(fmart_var.get(), 0)

    fbud_label_by_value = dict(FIELD_BG_BUDGET_CHOICES)
    fbud_value_by_label = {v: k for k, v in FIELD_BG_BUDGET_CHOICES}
    _fbud_default = build.field_bg_repack.DEFAULT_BUDGET_MB
    fbud_var = tk.StringVar(value=fbud_label_by_value.get(
        global_saved.get('field_bg_budget_mb', _fbud_default),
        fbud_label_by_value[_fbud_default]))

    def current_field_bg_budget_mb():
        return fbud_value_by_label.get(fbud_var.get(), _fbud_default)

    # ---- the ONE control, which drives the five above ------------------
    #
    # MUST come after all five, and after fbud_var in particular: _match_preset
    # reads every one of them, and a closure that runs at construction time
    # cannot reach a variable the enclosing scope has not bound yet. Defining
    # this block earlier raised NameError on `current_field_bg_budget_mb`.
    #
    # Reconstruct which preset the saved settings correspond to, so reopening
    # the dialog does not silently show "Custom" for a stock configuration.
    def _match_preset():
        cur = (current_field_bg_page_px(), current_field_bg_replace_only(),
               current_field_bg_max_pages(), current_field_bg_truecolor(),
               current_field_bg_budget_mb())
        for key, vals in FIELD_BG_PRESETS.items():
            if cur == vals:
                return key
        return 9                                          # Custom

    fpre_var = tk.StringVar(value=fpre_label_by_value[_match_preset()])

    def current_field_bg_preset():
        return fpre_value_by_label.get(fpre_var.get(), 9)

    def _apply_preset(*_a):
        """Push the chosen preset down into the five advanced controls."""
        key = current_field_bg_preset()
        vals = FIELD_BG_PRESETS.get(key)
        if vals is None:                                  # Custom: hands off
            return
        px, repl, maxp, part, bud = vals
        fbg_var.set(fbg_label_by_value[px])
        frepl_var.set(frepl_label_by_value[repl])
        fmaxp_var.set(fmaxp_label_by_value[maxp])
        ftc_var.set(ftc_label_by_value[part])
        fbud_var.set(fbud_label_by_value[bud])

    _applying_preset = []

    def _apply_preset_guarded(*a):
        _applying_preset.append(1)
        try:
            _apply_preset(*a)
        finally:
            _applying_preset.pop()

    def _preset_to_custom(*_a):
        """Touching any advanced control means the preset no longer holds."""
        if _applying_preset:
            return                       # we are the ones writing them
        if current_field_bg_preset() != _match_preset():
            fpre_var.set(fpre_label_by_value[_match_preset()])

    fpre_var.trace_add('write', _apply_preset_guarded)
    for _v in (fbg_var, frepl_var, fmaxp_var, ftc_var, fbud_var):
        _v.trace_add('write', _preset_to_custom)

    movie_label_by_value = dict(MOVIE_QUALITY_CHOICES)
    movie_value_by_label = {v: k for k, v in MOVIE_QUALITY_CHOICES}
    default_quality = build.movie_convert.QUALITY_DEFAULT
    initial_movie = global_saved.get('movie_quality', default_quality)
    if initial_movie not in movie_label_by_value:
        initial_movie = default_quality
    movie_var = tk.StringVar(value=movie_label_by_value[initial_movie])

    def current_movie_quality():
        return movie_value_by_label.get(movie_var.get(), default_quality)

    mfit_label_by_value = dict(MOVIE_FIT_CHOICES)
    mfit_value_by_label = {v: k for k, v in MOVIE_FIT_CHOICES}
    _mfit_default = build.movie_convert.FIT_DEFAULT
    _mfit = global_saved.get('movie_fit', _mfit_default)
    if _mfit not in mfit_label_by_value:
        _mfit = _mfit_default
    mfit_var = tk.StringVar(value=mfit_label_by_value[_mfit])

    def current_movie_fit():
        return mfit_value_by_label.get(mfit_var.get(), _mfit_default)

    mcol_label_by_value = dict(MOVIE_COLOUR_CHOICES)
    mcol_value_by_label = {v: k for k, v in MOVIE_COLOUR_CHOICES}
    _mcol_default = build.movie_convert.COLOUR_DEFAULT
    _mcol = global_saved.get('movie_colour', _mcol_default)
    if _mcol not in mcol_label_by_value:
        _mcol = _mcol_default
    mcol_var = tk.StringVar(value=mcol_label_by_value[_mcol])

    def current_movie_colour():
        return mcol_value_by_label.get(mcol_var.get(), _mcol_default)

    fps_var = tk.BooleanVar(value=bool(global_saved.get('fps_60', False)))
    m30_var = tk.BooleanVar(value=bool(global_saved.get('movie_30fps', False)))
    a360_var = tk.BooleanVar(value=bool(global_saved.get('analog_360', False)))
    norun_var = tk.BooleanVar(value=bool(global_saved.get('no_autorun', False)))
    # REMOVED: bgclr_var ('bg_clear', "Black 16:9 margins") and mclip_var
    # ('movie_clip', "Clip models to the movie"). Both retired -- FINDINGS-92
    # §6. A stale key left in settings.json by an older build is ignored: the
    # environment variables are no longer written and nothing reads them.
    #
    # The 16:9 field frame. Defaults ON because with widescreen off it cannot
    # write a word -- ff7nx_letterbox.enabled() and ff7nx_modelcull.enabled()
    # both fall through to ff7nx_ws.enabled().
    frame_var = tk.BooleanVar(value=bool(global_saved.get('field_frame', True)))
    nocheat_var = tk.BooleanVar(value=bool(global_saved.get('no_cheats', False)))

    lim_label_by_value = {v: l for v, l in LIMITER_FPS_CHOICES}
    lim_value_by_label = {l: v for v, l in LIMITER_FPS_CHOICES}
    _lim = global_saved.get('limiter_fps', 0)
    lim_var = tk.StringVar(value=lim_label_by_value.get(
        _lim, lim_label_by_value[0]))

    def current_limiter_fps():
        return lim_value_by_label.get(lim_var.get(), 0)

    # A settings.json written before this cleanup can hold `field`, `fit`,
    # `stretch` or a bare bool. All of them are known-bad builds and none of
    # them has a label any more, so they read back as Off rather than as
    # whatever the dropdown's first entry happens to be. Silently promoting
    # a saved `field` to the new `ws` would be worse: it would mean the user
    # gets a different build than the one they chose, without being told.
    ws_saved = global_saved.get('widescreen', '')
    # `ws-2d` was the 2D-only measurement build and `ws-3d` supersedes it.
    # Migrating rather than leaving it is deliberate: the label changed but
    # the VALUE did not, so a saved `ws-2d` silently kept selecting the old
    # build while the dropdown looked like it offered a new one. That cost a
    # hardware test.
    if ws_saved == 'ws-2d':
        ws_saved = build.ff7nx_ws.MODE_WS_3D
    if ws_saved is True or ws_saved in build.ff7nx_ws.LEGACY_MODES:
        ws_saved = ''
    elif ws_saved is False:
        ws_saved = ''
    # A saved value with no label in THIS dropdown must never silently become
    # Off. `WIDESCREEN_LABELS.get(x, OFF)` did exactly that: the migration
    # above rewrote `ws-2d` to `ws-3d`, `ws-3d` had no entry, the combo
    # displayed Off, and the next save wrote '' -- so the setting was wiped
    # on every launch and three hardware tests went to the wrong build.
    #
    # Falling back to the strongest 16:9 entry the dropdown actually has
    # makes this self-healing: any future value/label mismatch degrades to
    # "closest thing offered", not to "silently off".
    if ws_saved and ws_saved not in WIDESCREEN_LABELS:
        ws_saved = next((v for v, _ in reversed(WIDESCREEN_CHOICES) if v), '')
    ws_var = tk.StringVar(value=WIDESCREEN_LABELS.get(ws_saved,
                                                      WIDESCREEN_LABELS['']))

    def current_widescreen():
        return WIDESCREEN_BY_LABEL.get(ws_var.get(), '')

    # The field render target. Defaults to 1x rather than Off: with the
    # framing on and the buffer stock you get the vertical bands, and a
    # default that reproduces a known artefact is not a default.
    fbuf_saved = global_saved.get('field_buffer',
                                  build.ff7nx_fieldbuf.DEFAULT_SCALE)
    try:
        fbuf_saved = int(fbuf_saved)
    except (TypeError, ValueError):
        fbuf_saved = build.ff7nx_fieldbuf.DEFAULT_SCALE
    if fbuf_saved not in FIELD_BUF_LABELS:
        # Same rule as the widescreen dropdown: an unknown value must not
        # silently become Off, because Off is the one that bands. Fall back
        # to the largest entry that is not greater than what was saved.
        fbuf_saved = max((v for v in FIELD_BUF_LABELS if v <= fbuf_saved),
                         default=build.ff7nx_fieldbuf.DEFAULT_SCALE)
    fbuf_var = tk.StringVar(value=FIELD_BUF_LABELS[fbuf_saved])

    def current_field_buffer():
        return FIELD_BUF_BY_LABEL.get(fbuf_var.get(),
                                      build.ff7nx_fieldbuf.DEFAULT_SCALE)

    scaler_saved = global_saved.get('scaler', '')
    if scaler_saved not in SCALER_LABELS:
        scaler_saved = ''
    scaler_var = tk.StringVar(value=SCALER_LABELS[scaler_saved])

    def current_scaler():
        return SCALER_BY_LABEL.get(scaler_var.get(), '')

    fxaa_saved = global_saved.get('fxaa', '')
    if fxaa_saved not in FXAA_LABELS:
        fxaa_saved = ''
    fxaa_var = tk.StringVar(value=FXAA_LABELS[fxaa_saved])

    def current_fxaa():
        return FXAA_BY_LABEL.get(fxaa_var.get(), '')

    vshader_saved = global_saved.get('video_shader', '')
    if vshader_saved not in VIDEO_SHADER_LABELS:
        vshader_saved = ''
    vshader_var = tk.StringVar(value=VIDEO_SHADER_LABELS[vshader_saved])

    def current_video_shader():
        return VIDEO_SHADER_BY_LABEL.get(vshader_var.get(), '')

    if not have_nso:
        # These need exefs/main, which a bare workingdir/ does not have.
        #
        # 16:9 is NOT in this list any more, and that is the point of the
        # content stage: it edits flevel.lgp and nothing else, so it works
        # on a workingdir-only setup where every module patch is
        # unavailable.
        fps_var.set(False)
        m30_var.set(False)
        a360_var.set(False)
        norun_var.set(False)
        nocheat_var.set(False)
        lim_var.set(lim_label_by_value[0])

    # ---- the dialog -----------------------------------------------------
    # Grouped by what the setting is ABOUT, not by which module implements
    # it, because that is how someone looks for one.
    SETTINGS_SECTIONS = [
        ('Display', [
            ('combo', '16:9 widescreen', ws_var,
             [l for _, l in WIDESCREEN_CHOICES],
             '“16:9 widescreen” is the whole thing: it bakes the widescreen '
             'mod’s per-field camera ranges into flevel.lgp (Cosmos Limit '
             'Break ships a config — on it, that takes 341 of 711 fields to '
             '647), widens the render target to 16:9, opens the field '
             'background’s tile window on all four sides, and ships the two '
             'vertex shaders that carry the scale. Needs a widescreen mod '
             'enabled AND exefs/main from a full game dump.\n\n'
             '“Data only” bakes the camera ranges and stops there. It never '
             'opens exefs/main, so THE PICTURE STAYS 4:3 — it exists to test '
             'the data half on its own, or to prepare flevel on a machine '
             'with no dump. If you pick it and wonder why nothing changed on '
             'the console, that is why; the build log says so too.\n\n'
             'FIXED since this text was written: the battle scene now draws '
             'to the bottom of the frame instead of stopping at the UI band; '
             'the battle-entry swirl fills the frame instead of squeezing '
             'the 16:9 picture into 4:3; and the battle fade and the summon '
             'and limit-break flashes cover the whole frame; and menu and '
             'dialogue boxes keep the border on the side that faces screen '
             'centre; and the intro/prelude no longer SMEARS credit text '
             'into the side margins -- its fade covers the whole frame and '
             'its colour buffer is cleared every frame. Those follow this '
             'setting automatically and have no switch of their own.\n\n'
             'KNOWN GAPS with full widescreen: the battle fade-in animation '
             'sweeps only the top ~70% before snapping, because its strip '
             'stride still scales to the old 332-unit height; the victory '
             'fade-to-black and some battle flashes are still 4:3; and the '
             'fields with no camera range in the mod’s config can show a '
             'black bar at the left edge when the camera pans; and in the '
             'intro/prelude, credit lines are visible sitting in the side '
             'margins while they wait to slide in. The smear is gone, but '
             'the margins are not painted black yet -- the gate that would '
             'do it needs a flag the game does not keep. See HANDOFF-104.'
             '\n\n'
             'CORRECTED: this text used to say the clipped window borders '
             'were “NOT a widescreen bug”, on the grounds that the window '
             'geometry is a stock data table this build never touches. The '
             'geometry was innocent; the CLIP was not. The scale lives in '
             'the vertex shader, so 2D geometry is scaled but a window’s own '
             'viewport rect — computed on the CPU — is not, and the two only '
             'agree at the centre of the screen. A box left of centre lost '
             'its right border, a box right of centre lost its left one. '
             'Fixed, and it follows this setting. See FINDINGS-103.\n\n'
             'AND CORRECTED AGAIN: the first fix for that pointed the window '
             'clip at the whole screen. Borders came back, but dialogue text '
             'started outliving its box — the text kept drawing over the '
             'field while the box shrank away — because that same clip is '
             'what hides a window’s contents as it opens and closes. It is '
             'now SCALED rather than removed, so the clip still does its job '
             'and does it in the right place. If you saw text linger after '
             'its box, that build is what did it, and this one does not.',
             True),
            ('combo', 'Field render resolution', fbuf_var,
             [l for _, l in FIELD_BUF_CHOICES],
             'THE FIX FOR THE VERTICAL BANDS. The port draws the field into '
             'a hidden 320×240 buffer, because at 320 pixels the '
             'pre-rendered background lands exactly 1:1 — one source texel '
             'on one pixel — and the background scaler reconstructs it from '
             'there. Widescreen squeezed 853 game units through those same '
             '320 pixels, so a 16-texel tile was drawn into 12, and that '
             'resample beats every 3 buffer pixels: the 12-pixel bands glued '
             'to the screen. Widening the buffer puts the 1:1 back.\n\n'
             '1× is the one confirmed on hardware, and it also hands the '
             'field back the 25% of horizontal resolution widescreen had '
             'been costing it. 2× and 3× supersample the whole field pass on '
             'top of that; 3× is exactly 16:9 and renders the field at '
             'native 720p in handheld, but it magnifies the background with '
             'the hardware sampler before the scaler sees it, so it is a '
             'different look rather than simply a better one — try Crisp '
             'with it.\n\n'
             'MEMORY: there are eight of these buffers, so 1× costs +0.8 MB '
             'over stock, 2× costs +10 MB and 3× costs +26 MB — out of the '
             'same pool the field background PAGES allocate from. The page '
             'budget below was measured at the stock size, so at 2× or 3× a '
             'heavy field (7–8 pages: the Sector 7 slums, nmkin_*) can run '
             'out, and when it does the loader gives up on the first page it '
             'cannot fit and every page after it draws nothing at all — a '
             'flat coloured block where the art should be. Lower the page '
             'budget or come back to 1× if you see that.\n\n'
             'Only does anything with “16:9 widescreen” selected above. '
             'See HANDOFF-51.', True),
        ]),
        ('Display extras', [
            ('check', 'Full-height 16:9 field', frame_var, None,
             'The field is drawn 448 of 480 game units tall and the driver '
             'PAINTS the missing 32 as two black quads over the finished '
             'frame — set_driver_mode’s field branch is the only one of '
             'fifteen that stores a non-zero letterbox height, which is why '
             'battle and the menus reach the top of the screen and the field '
             'does not, and why characters vanish under the bars instead of '
             'being drawn over them.\n\n'
             'This stops painting them, opens the field viewport to the full '
             '480, and moves the background and its sprites down 8 tile '
             'units so the window sits exactly on FF7’s own camera clamp '
             'rather than 8 units past the edge of the art. That last part '
             'is what stops a black band appearing at the bottom of a map '
             'when you walk down to it: measured over the built archive, 561 '
             'of 699 fields run out by exactly 8 units without it and 3 with '
             'it.\n\n'
             'It also shifts the FMV quad by the same amount, so a video '
             'that hands straight over to gameplay does it seamlessly — '
             'the bars drop and the view expands instead of the picture '
             'jumping 24 px. Those two are one change and cannot be split: '
             'move the field without the movie and the cut looks worse than '
             'it did before. FFNx’s numbers (enable_uncrop, '
             'ff7_field_center).\n\n'
             'It also paints the FMV’s own 4:3 margins. FF7 keeps videos at '
             '4:3, so in a 16:9 frame there is black to the left and right of '
             'the picture and a thin band above and below it — and a field '
             'model standing near the edge used to be drawn straight over it, '
             'sword and legs hanging outside the video. Four opaque black '
             'quads go down last, over the finished frame, so the overhang '
             'passes UNDER them the way a real letterbox works. Nothing is '
             'clipped and no model disappears. Only while a video is actually '
             'playing. See FINDINGS-92.\n\n'
             'NOT on this switch: the model cull box. It is 4:3-sized and '
             'gets widened to the 16:9 frame whenever 16:9 is on, with or '
             'without this box ticked. That one is a plain bug — NPCs '
             'switched off while still inside the picture — and there is '
             'no setting in which you would want it back.\n\n'
             'Seven words, an eighteen-word cave for the movie quad and a '
             'seventy-six-word one for the margin bars, all in dead alignment '
             'padding and byte-exactly reversible. Only does anything with '
             '“16:9 widescreen” selected above — at 4:3 the letterbox is '
             'the framing the game was authored in, and there are no margins '
             'to paint. See FINDINGS-88 and FINDINGS-92.', True),
            # REMOVED, both retired on hardware results -- FINDINGS-92 §6.
            #
            # "Clip models to the movie" narrowed the GL scissor to the central
            # 4:3 while a movie played. It could not work: the scissor it
            # narrowed also caught the PRESENTATION BLIT, so the back buffer's
            # margins were never written and froze the last field frame drawn
            # before the FMV. That job is now done by ff7nx_moviebars, which
            # paints the four margins as opaque black quads in the flip path --
            # last, over the finished frame -- so overhang goes UNDER the black
            # instead of being clipped, and no frame state changes at all.
            #
            # "Black 16:9 margins" made gfx_drv_setbg store black instead of
            # the colour it was handed. Measured on hardware and it changed
            # nothing, because the flat margin colour is not the clear colour.
            # ff7nx_marginart / ff7nx_marginpal fixed that at the source.
        ]),
        ('Shaders', [
            ('combo', 'Background scaler', scaler_var,
             [l for _, l in SCALER_CHOICES],
             'Replaces the 2xSaI / HQ4x pair the port uses to turn the '
             'low-resolution field buffer into screen pixels — the thing '
             'that makes the pre-rendered maps look soft. HD was tuned '
             'against this game’s own art by reconstructing downsampled '
             'flevel crops, which is why it is the recommendation over the '
             'generic pixel-art kernels. These used to have to be copied '
             'onto the card by hand; now the build ships them, and removes '
             'them again when you switch back to Stock.\n\n'
             'How much this matters depends on the setting above: at 1× the '
             'scaler is doing all the magnification and the choice is very '
             'visible; at 3× most of it has already happened and every '
             'kernel converges towards smooth.', False),
            ('combo', 'Full-screen anti-aliasing', fxaa_var,
             [l for _, l in FXAA_CHOICES],
             'The port runs FXAA over the whole frame. HD is a retuned '
             'version; Off is sharper and cheaper and leaves 3D model edges '
             'jaggier. Independent of the scaler above — it is a different '
             'shader on a different pass.', False),
            ('combo', 'Movie shader', vshader_var,
             [l for _, l in VIDEO_SHADER_CHOICES],
             'The shader the port draws FMVs through. HD is '
             'custom_shaders/hd_video. One file, independent of everything '
             'else here, and it does not re-encode anything — the movies '
             'themselves are built by the settings further down.', False),
        ]),
        ('Frame rate', [
            ('check', '60 FPS patches', fps_var, None,
             'The whole 60 FPS set. Menus, battles, field and world.', True),
            ('check', '30 FPS FMVs', m30_var, None,
             'Builds every movie at 30 fps and halves the game\u2019s movie '
             'frame counter to match. Independent of the switch above.', True),
            ('combo', 'Frame pacing headroom', lim_var,
             [l for _, l in LIMITER_FPS_CHOICES],
             'Each limiter waits a frame\u2019s worth of time measured from '
             'the START of the frame, but the frame is not finished when it '
             'releases \u2014 the tail after it is added on top, so the real '
             'period is longer than 1/60 and the game produces slightly under '
             '60 frames a second. Presents are capped at 60, so the shortfall '
             'shows up as 57\u201359. Aiming a little higher cancels the '
             'tail. USE THE LOWEST VALUE THAT HOLDS A SOLID 60: above that '
             'the game produces more than 60 a second and runs FAST, which '
             'the display will hide from you.', True),
        ]),
        ('Controls', [
            ('check', '360\u00b0 field movement', a360_var, None,
             'Walk in the exact stick direction instead of snapping to '
             'eight. Needs cave space the full 60 FPS preset does not leave '
             'spare \u2014 if the build stops with \u201ccave overflows '
             '.rodata\u201d, this is why.', True),
            ('check', 'Disable Auto-Run', norun_var, None,
             'The port holds the run button for you once the stick is pushed '
             'past 90%. This removes that, so walk and run are the button '
             'again. Direction is unaffected — that uses a separate, '
             'lower threshold.', True),
            ('check', 'No Cheats', nocheat_var, None,
             'Makes clicking the RIGHT STICK do nothing. On this port that '
             'click fills HP, MP and the limit gauge, and one accidental '
             'press can spoil a playthrough. Left stick click (3× speed) '
             'is left alone.', True),
        ]),
        ('Textures', [
            ('combo', 'Field backgrounds', fpre_var,
             [l for _, l in FIELD_BG_PRESET_CHOICES],
             'THE ONLY ONE OF THESE YOU NEED. It sets the five controls in '
             '“Field backgrounds (advanced)” below.\n\n'
             'Each preset pins the four safety controls to the same values '
             '— replace-only on, ceiling 12 pages, all or nothing, '
             'budget unlimited — because that is the combination which '
             'cannot ask the loader for more textures than the stock game '
             'does. The preset chooses only the PAGE SIZE, which is the one '
             'axis that is a genuine trade rather than a guess.\n\n'
             'Conservative (256px) is truecolor at vanilla resolution: it '
             'removes palette banding but gives NO sharpness gain, because '
             'the mod’s art is downscaled back to the size it was '
             'upscaled from. Balanced (384px) and Maximum (512px) are real '
             'detail gains.\n\n'
             'Picking anything in the advanced section moves this to '
             'Custom.', True),
        ]),
        ('Field backgrounds (advanced)', [
            ('combo', 'Field background page size', fbg_var,
             [l for _, l in FIELD_BG_PAGE_PX_CHOICES],
             'Sets the size AND the colour depth of the field background '
             'pages, so an upscale mod\u2019s art can actually be shown.\n\n'
             'Cost per page is 6\u00d7px\u00b2 \u2014 the pixels plus the '
             '32bpp surface the engine builds from them \u2014 and the '
             'heaviest field in the game has 12 pages. That is the whole '
             'trade: 128px costs 0.09 MB a page, 512px costs 1.50 MB, and '
             '12 \u00d7 1.50 = 18 MB is exactly where black bars were '
             'measured on hardware.\n\n'
             '256px is the one to try first. It keeps vanilla resolution but '
             'makes every page TRUECOLOR, which removes palette banding, the '
             'colour-key restriction and neighbouring-palette substitution, '
             'and it is cheap enough that every page on every field fits \u2014 '
             'so no field is ever left half-upgraded. 128px is the setting '
             'for when memory is the problem: it is cheaper than the paletted '
             'pages the game already ships, at the cost of half the '
             'background resolution.\n\n'
             'Sizes other than 256 patch exefs/main AND rewrite flevel.lgp, '
             'and both halves are needed. 256px needs no module patch at all, '
             'so it is the only one testable without a full game dump.', True),
            ('combo', 'Field background TRUECOLOR pages', ftc_var,
             [l for _, l in FIELD_BG_TRUECOLOR_CHOICES],
             'How many pages of a field may be promoted from 8-bit paletted '
             'to 16-bit truecolor.\n\n'
             'A truecolor page costs 0.38 MB against a paletted page\u2019s '
             '0.31 MB \u2014 seven hundredths of a megabyte \u2014 so this '
             'is cheap in memory. What it is NOT cheap in is PAGES: a '
             'promotion ADDS a page, because the original paletted one must '
             'stay alive for every cell that could not move (colour-key '
             'cells, fx-page tiles).\n\n'
             'RAISE THIS WITH PAGE GROWTH ON "NO GROWTH", NOT "OFF". '
             'Compaction rides with no-growth and nothing else \u2014 it is '
             'the pass that pays for the promoted pages out of the originals, '
             'which are mostly empty afterwards. MEASURED: with it on, 1,165 '
             'pages freed and 5.6 pages per field; with growth "Off" it does '
             'not run, the count goes to 7.5 per field, and '
             'field_load_textures starts abandoning the load \u2014 which is '
             'what black squares are.\n\n'
             '3 is what shipped, and it was never measured as a ceiling \u2014 '
             'the numbers behind it came from 512px experiments whose model '
             'has since been retracted. What it was really guarding against '
             'was the 256-tiles-per-page overrun (FINDINGS-110), and that is '
             'now prevented outright by field_bg_pagecap.\n\n'
             'This control previously said "Field background promotion" and '
             'did nothing at all: it fed field_bg_repack.all_or_nothing(), '
             'and the pass that reads it stopped being called. Both settings '
             'produced byte-identical builds.',
             True),
            ('combo', 'Field background page growth', frepl_var,
             [l for _, l in FIELD_BG_REPLACE_ONLY_CHOICES],
             'Turn this on if you want a BIG page size without black '
             'squares. It is the one setting that attacks the cause rather '
             'than rationing the symptom.\n\n'
             'Promoting a page normally ADDS one. The promoted cells go to a '
             'new truecolor page, but the original paletted page has to stay '
             'alive for every tile that could not move — cells with a '
             'colour key, tiles that animate from an fx page, cells the '
             'mod draws transparent. Two textures where there was one, and '
             'every texture is an allocation the loader can fail on.\n\n'
             'A page whose tiles ALL move is different: nothing references it '
             'afterwards, so it is freed and the count does not grow. '
             'Promoting only those holds the texture count at vanilla’s, '
             'which is what lets the page SIZE go up safely.\n\n'
             'The trade is that less art gets promoted. Turn it on when the '
             'page ceiling is what is holding you back.', True),
            ('combo', 'Cosmos widescreen margin art', fmart_var,
             [l for _, l in FIELD_BG_MARGIN_ART_CHOICES],
             'Fills the 16:9 side bands with the mod\u2019s own widescreen '
             'art instead of a flat colour. Needs Cosmos Limit Break (or any '
             'mod that ships field .dds) enabled \u2014 with nothing to read '
             'it does nothing.\n\n'
             'Those bands are MISSING TEXTURES, not authored letterbox. '
             'Cosmos points margin tiles at cells that are blank in the '
             'vanilla page and painted in its upscale. FFNx loads the .dds '
             'and draws scenery there; this port has no loader for it, so the '
             'tile samples the blank placeholder and the band comes out one '
             'flat colour \u2014 tan, green or plum depending on the field. '
             'Measured over 45 fields: 81% of those tiles have real art in '
             'the .dds.\n\n'
             'This writes that art into the paletted page as INDICES, so it '
             'costs no new page and no new texture, and cannot bring the '
             'black squares back.\n\n'
             'MARGIN + INTERIOR does the same thing inside the 4:3 picture. '
             'That matters because the repack can only reach an interior cell '
             'by PROMOTING its page to truecolor, and three things forbid '
             'that — a colour key on the cell, an fx page sharing one '
             'u,v, or the mod’s own art being transparent there. On a '
             'real build that is ~138,000 tiles left drawing vanilla INSIDE '
             'fields the log already counts as covered, which is why the '
             'centre still looks stock and why it looks patchy rather than '
             'uniformly old. Writing indices into the page that is already '
             'there sidesteps all three: index 0 stays index 0, so the colour '
             'key survives and the fx pairing stays valid.\n\n'
             'It is bounded by what the mod ships. Only layer 1 (the static '
             'scenery) is touched, so no animation can be left half-Cosmos '
             'and flicker; a cell drawn with two palettes is skipped, because '
             'a paletted page is one index array and there is no single right '
             'answer for it; and a cell the mod does not cover keeps its '
             'vanilla content rather than being painted black. Expect a large '
             'but partial lift, not a clean sweep.\n\n'
             'Runs BEFORE the background repack, because the mod names its '
             'art against the ORIGINAL page numbering and the repack '
             'renumbers and compacts.', False),
            ('combo', 'Field background max pages per field', fmaxp_var,
             [l for _, l in FIELD_BG_MAX_PAGES_CHOICES],
             'The ceiling that actually decides whether you get black '
             'squares. Read this one before the memory budget.\n\n'
             'Every page present in a field becomes a TEXTURE, and '
             'field_load_textures gives up on the whole loop at the first '
             'one it cannot allocate — every page after that draws '
             'nothing. So what breaks is the NUMBER of pages, not the number '
             'of megabytes.\n\n'
             'The repack ADDS pages rather than replacing them: a page can '
             'only be freed once nothing points at it, and enormous numbers '
             'of tiles keep their paletted page because their cell has a '
             'colour key or they animate from an fx page. Measured on a real '
             'build: 1,697 new pages against 184 freed, about +2.3 per '
             'field, with one field going from 10 pages to 17 — more '
             'than any field the stock game ships.\n\n'
             'Vanilla’s heaviest field is 12 pages and only 5 fields '
             'reach it, so 12 is the order the port was provisioned for. '
             'Raise it to hunt for the true ceiling; the build log names '
             'every field that goes over.', True),
            ('combo', 'Field background memory budget', fbud_var,
             [l for _, l in FIELD_BG_BUDGET_CHOICES],
             'A hard ceiling on the TRUECOLOR texture memory one field may '
             'use, in megabytes of RUNTIME cost \u2014 6\u00d7px\u00b2 per '
             'page, i.e. the pixels plus the 32bpp surface the engine builds '
             'from them.\n\n'
             'IT WAS DEAD UNTIL NOW, and that is worth knowing because the '
             'log has been quoting it for months. It was read only by '
             'field_bg_repack.upgrade(), and upgrade() stopped being called '
             'when field_bg_dense replaced it \u2014 zero references to the '
             'budget in that module. Any value you picked produced the same '
             'build. It is now enforced where the promotion actually '
             'happens.\n\n'
             'THIS IS THE RIGHT UNIT ONCE THE PAGE SIZE MOVES. A ceiling in '
             'PAGES means something four times bigger the moment you go from '
             '256px to 512px \u2014 the same 12 pages cost 4.56 MB at 256 '
             'and 18.00 MB at 512. Bytes do not do that:\n\n'
             '    budget      at 256px        at 512px\n'
             '    3.0 MB      8 pages         2 pages\n'
             '    4.5 MB     12 pages         3 pages\n'
             '    6.0 MB     16 pages         4 pages\n\n'
             'For reference, the last build with no black squares measured '
             'mean 1.87 MB per field and a heaviest field of 4.75 MB. The '
             '512px build that DID show black squares measured mean 4.72 MB '
             'and a heaviest of 12.31 MB. Somewhere between those is the '
             'ceiling field_load_textures actually has, and this is the '
             'control that bisects it.\n\n'
             'Unlimited is right at 256px, where a truecolor page is only '
             '0.07 MB more than the paletted one it replaces. At 512px it is '
             'not.',
             True),
            ('combo', 'Field model texture cap', cap_var,
             [l for _, l in FIELD_TEX_CAP_CHOICES],
             'Downscales oversized char.lgp / world_us.lgp model textures.',
             False),
            ('combo', 'Battle background cap (Avalanche Arisen)', bg_cap_var,
             [l for _, l in BATTLE_BG_TEX_CAP_CHOICES],
             'Scoped to Arisen\u2019s own tiles. Everything else in '
             'battle.lgp stays at the proven 256px.', False),
        ]),
        ('Movies', [
            ('combo', 'Video quality', movie_var,
             [l for _, l in MOVIE_QUALITY_CHOICES],
             'Applies to converted FMVs. Higher costs build time and card '
             'space; frame rate is set by the mod, not here.', False),
            ('combo', 'Video size', mfit_var,
             [l for _, l in MOVIE_FIT_CHOICES],
             'MEASURED on hardware: the panel shows the game in a 960x672 '
             'box, so a 1280x896 FMV can only ever land 56% of its pixels '
             'on screen \u2014 the rest are discarded by one bilinear tap in '
             'video_p.glsl. \u201cDisplayed area\u201d resamples here with '
             'Lanczos so the console draws 1:1 and throws nothing away.',
             True),
            ('combo', 'Video colour', mcol_var,
             [l for _, l in MOVIE_COLOUR_CHOICES],
             'The port\u2019s movie shader hardcodes BT.709. FMV packs made '
             'from FF7\u2019s standard-definition originals are BT.601, so '
             'without this their colour is slightly desaturated.', False),
        ]),
    ]

    def open_settings():
        win = tk.Toplevel(root)
        win.title('Settings')
        win.configure(bg=BG_APP)
        win.transient(root)
        # Vertically resizable, horizontally not: the blurbs are wrapped at a
        # fixed 560px, so a wider window buys nothing and a narrower one
        # clips them. Height is the axis that matters -- the dialog outgrew
        # a laptop screen once Display and Shaders arrived.
        win.resizable(False, True)

        # Done sits in a FIXED footer, packed first so it keeps its space,
        # rather than at the bottom of the scrolled content. A button you
        # have to scroll to find is a button people do not find.
        footer = ttk.Frame(win, padding=(16, 12))
        footer.pack(side='bottom', fill='x')
        ttk.Button(footer, text='Done', command=win.destroy).pack(side='right')

        # `make_scrollable` is defined further down this same function, so it
        # is not bound yet at definition time -- but open_settings only runs
        # when the button is clicked, by which point it is. It gives us the
        # wheel handling and the scrollbar that hides itself when everything
        # already fits, so a short settings list looks exactly as it did.
        scrollarea = ttk.Frame(win)
        scrollarea.pack(side='top', fill='both', expand=True)
        canvas, holder = make_scrollable(scrollarea)
        body = ttk.Frame(holder, padding=16)
        body.pack(fill='both', expand=True)

        # A running counter, not `section * 100 + row`. The sparse scheme
        # put the Done button at row 9999 and Tk refused it -- grid rows are
        # bounded, and leaving gaps buys nothing because grid collapses empty
        # rows anyway. Counting is also the only version that cannot go out
        # of range however many settings get added later.
        # The blurbs span both columns at wraplength 560, so they set the
        # dialog's width; giving column 1 the slack is what makes the
        # `sticky='e'` on each combobox actually right-align it against that
        # width instead of leaving it hugging its label.
        body.columnconfigure(1, weight=1)

        r = 0
        for si, (section, rows) in enumerate(SETTINGS_SECTIONS):
            ttk.Label(body, text=section.upper(), style='Sub.TLabel'
                      ).grid(row=r, column=0, columnspan=2,
                             sticky='w', pady=(0 if si == 0 else 16, 6))
            r += 1
            for kind, label, var, values, blurb, needs in rows:
                dim = needs and not have_nso
                if kind == 'check':
                    w = ttk.Checkbutton(body, text=label, variable=var)
                    w.grid(row=r, column=0, columnspan=2, sticky='w')
                else:
                    ttk.Label(body, text=label).grid(row=r, column=0,
                                                    sticky='w', padx=(0, 12))
                    w = ttk.Combobox(body, textvariable=var, values=values,
                                     state='readonly', width=44)
                    w.grid(row=r, column=1, sticky='e')
                if dim:
                    w.state(['disabled'])
                r += 1
                note = blurb + ('   (needs exefs/main from a full game dump)'
                                if dim else '')
                ttk.Label(body, text=note, style='Sub.TLabel',
                          wraplength=560, justify='left'
                          ).grid(row=r, column=0, columnspan=2,
                                 sticky='w', pady=(1, 0))
                r += 1

        win.bind('<Escape>', lambda _e: win.destroy())

        # make_scrollable binds the wheel with bind_all on <Enter> and drops
        # it on <Leave>. Closing the dialog with the pointer still over it
        # means <Leave> never fires, and the global binding is left pointing
        # at a destroyed canvas -- the next wheel event anywhere in the app
        # then raises TclError. Drop it on destroy as well. The main window's
        # panes rebind their own on the next <Enter>, so nothing is lost.
        def _drop_wheel(evt):
            if evt.widget is win:
                try:
                    win.unbind_all('<MouseWheel>')
                except tk.TclError:                    # pragma: no cover
                    pass
        win.bind('<Destroy>', _drop_wheel)

        # Keyboard scrolling. The wheel is bound by make_scrollable and only
        # while the pointer is over the canvas; these work wherever focus is,
        # which matters because a readonly Combobox eats the pointer.
        def _scroll(units=0, pages=0, to=None):
            def go(_evt=None):
                # A readonly Combobox uses Up/Down to change its value and
                # does NOT return 'break', so the toplevel binding would fire
                # as well and the dialog would scroll every time someone
                # picked an option. Let the focused widget win.
                try:
                    focused = win.focus_get()
                except KeyError:                       # pragma: no cover
                    focused = None
                if isinstance(focused, ttk.Combobox):
                    return None
                if to is not None:
                    canvas.yview_moveto(to)
                elif pages:
                    canvas.yview_scroll(pages, 'pages')
                else:
                    canvas.yview_scroll(units, 'units')
                return 'break'
            return go

        for seq, fn in (('<Up>', _scroll(units=-2)),
                        ('<Down>', _scroll(units=2)),
                        ('<Prior>', _scroll(pages=-1)),
                        ('<Next>', _scroll(pages=1)),
                        ('<Home>', _scroll(to=0.0)),
                        ('<End>', _scroll(to=1.0))):
            win.bind(seq, fn)

        # Size to the content, then cap the height so the window always fits
        # on the screen it is opening on. Without the cap a long settings
        # list simply grew off the bottom, which is the bug this replaces.
        win.update_idletasks()
        # `body` is a ttk.Frame with padding=16, and a padded frame's
        # requested size ALREADY includes its padding -- adding it again here
        # is how a dialog ends up with 32px of dead space down one side.
        want_w = body.winfo_reqwidth()
        want_h = body.winfo_reqheight() + footer.winfo_reqheight()
        max_h = int(win.winfo_screenheight() * 0.86)
        height = min(want_h, max_h)
        if height < want_h:
            # The scrollbar is about to appear inside the canvas width, so
            # widen by its thickness rather than letting it eat 16px of the
            # blurbs.
            want_w += 18
        win.geometry('%dx%d' % (want_w, height))
        win.minsize(want_w, min(420, height))
        win.maxsize(want_w, want_h)

        # centre on the main window, then pull back on-screen if the cap
        # still leaves it hanging off the bottom
        win.update_idletasks()
        x = root.winfo_rootx() + (root.winfo_width() - want_w) // 2
        y = root.winfo_rooty() + 60
        if y + height > win.winfo_screenheight() - 40:
            y = max(20, win.winfo_screenheight() - height - 40)
        win.geometry('+%d+%d' % (max(x, 0), max(y, 0)))
        win.grab_set()
        win.focus_set()

    ttk.Button(header, text='\u2699  Settings', command=open_settings
               ).pack(side='right')

    # ---------- settings persistence -------------------------------------
    # Everything the user can touch is written back to settings.json as soon
    # as it changes AND again on window close, not only when Build runs.
    # Persisting on Build alone meant that ticking mods and then quitting --
    # the normal way to set up before a later session -- silently threw the
    # selection away.
    #
    # `_ui_ready` suppresses the writes that fire while the widgets are still
    # being constructed (creating a row's Checkbutton, or select() populating
    # the first mod's option vars), so a half-built UI can never overwrite a
    # good settings file.
    _ui_ready = [False]

    def snapshot_settings():
        persist = {}
        for mod in mods:
            var = enabled.get(mod.filename)
            persist[mod.filename] = {
                'enabled': bool(var.get()) if var is not None else True,
                'options': settings_by_mod.get(mod.filename, {}),
            }
        persist['__global__'] = {'field_tex_cap': current_field_tex_cap(),
                                 'battle_bg_tex_cap': current_battle_bg_tex_cap(),
                                 'field_bg_page_px':
                                     current_field_bg_page_px(),
                                 'field_bg_budget_mb':
                                     current_field_bg_budget_mb(),
                                 'field_bg_truecolor_pages':
                                     current_field_bg_truecolor(),
                                 'field_bg_max_pages':
                                     current_field_bg_max_pages(),
                                 'field_bg_replace_only':
                                     current_field_bg_replace_only(),
                                 'field_bg_preset':
                                     current_field_bg_preset(),
                                 'margin_art':
                                     current_field_bg_margin_art(),
                                 # Marks this file as written by the split
                                 # Off/256 ladder, so 256 is never read back
                                 # as the old "off". See initial_fbg_value.
                                 'field_bg_ladder_v2': True,
                                 # FINDINGS-122/123. No widget yet, but these
                                 # MUST be written back: this dict is rebuilt
                                 # from scratch on every save, so a key that is
                                 # only read would be silently dropped the next
                                 # time the user touches any other control.
                                 'field_bg_window_cap':
                                     bool(_global_setting(
                                         'field_bg_window_cap',
                                         _global_setting(
                                             'field_bg_single_screen_cap',
                                             True))),
                                 'field_bg_fx_split':
                                     bool(_global_setting(
                                         'field_bg_fx_split', False)),
                                 'field_bg_compact_frame_safe':
                                     bool(_global_setting(
                                         'field_bg_compact_frame_safe',
                                         True)),
                                 'field_bg_clamp_palettes':
                                     bool(_global_setting(
                                         'field_bg_clamp_palettes', True)),
                                 'fps_60': bool(fps_var.get()),
                                 'movie_quality': current_movie_quality(),
                                 'movie_fit': current_movie_fit(),
                                 'movie_colour': current_movie_colour(),
                                 'movie_30fps': bool(m30_var.get()),
                                 'analog_360': bool(a360_var.get()),
                                 'no_autorun': bool(norun_var.get()),
                                 'field_frame': bool(frame_var.get()),
                                 'no_cheats': bool(nocheat_var.get()),
                                 'limiter_fps': current_limiter_fps(),
                                 'widescreen': current_widescreen(),
                                 'field_buffer': current_field_buffer(),
                                 'scaler': current_scaler(),
                                 'fxaa': current_fxaa(),
                                 'video_shader': current_video_shader()}
        return persist

    def save_settings_now(*_args):
        """Write settings.json. Never allowed to take the UI down with it:
        a failed save is a nuisance, an unhandled exception in a Tk trace
        callback is a broken window."""
        if not _ui_ready[0]:
            return
        try:
            build.save_settings(SETTINGS, snapshot_settings())
        except Exception as exc:                      # noqa: BLE001
            print('could not save settings: %s' % exc, file=sys.stderr)

    fps_var.trace_add('write', save_settings_now)
    ws_var.trace_add('write', save_settings_now)
    cap_var.trace_add('write', save_settings_now)
    bg_cap_var.trace_add('write', save_settings_now)
    fbg_var.trace_add('write', save_settings_now)
    fbud_var.trace_add('write', save_settings_now)
    ftc_var.trace_add('write', save_settings_now)
    fmaxp_var.trace_add('write', save_settings_now)
    frepl_var.trace_add('write', save_settings_now)
    # MISSING UNTIL NOW, and `test_gui_settings.py` was failing on it:
    # changing "Cosmos widescreen margin art" was not written to settings.json
    # when you changed it, only when a build happened to save the whole
    # snapshot. Close the window in between and the choice was lost.
    fmart_var.trace_add('write', save_settings_now)
    fpre_var.trace_add('write', save_settings_now)
    movie_var.trace_add('write', save_settings_now)
    mfit_var.trace_add('write', save_settings_now)
    mcol_var.trace_add('write', save_settings_now)
    m30_var.trace_add('write', save_settings_now)
    a360_var.trace_add('write', save_settings_now)
    norun_var.trace_add('write', save_settings_now)
    frame_var.trace_add('write', save_settings_now)
    nocheat_var.trace_add('write', save_settings_now)
    lim_var.trace_add('write', save_settings_now)
    # The four added with the field-buffer work. Without these they are
    # only written on Build and on window close, which is enough to be
    # correct and not enough to be obvious -- every other control here
    # saves the moment it changes.
    fbuf_var.trace_add('write', save_settings_now)
    scaler_var.trace_add('write', save_settings_now)
    fxaa_var.trace_add('write', save_settings_now)
    vshader_var.trace_add('write', save_settings_now)

    # ---------- action bar (bottom, packed FIRST so it is never clipped) ----
    actions = ttk.Frame(outer)
    actions.pack(side='bottom', fill='x', pady=(12, 0))

    build_btn = ttk.Button(actions, text='► Build SD Output',
                           style='Build.TButton')
    build_btn.pack(side='right')
    open_btn = ttk.Button(actions, text='Open output folder',
                          command=lambda: _reveal(SDOUT_DIR))
    open_btn.pack(side='right', padx=(0, 8))
    open_btn.state(['disabled'])

    bar = ttk.Progressbar(actions, mode='determinate')
    bar.pack(side='left', fill='x', expand=True, pady=6)
    statuslabel = ttk.Label(actions, text='ready', style='Sub.TLabel')
    statuslabel.pack(side='left', padx=10)

    # ---------- resizable split: mods/options area above, log below --------
    # A vertical PanedWindow gives a real drag handle on the divider right
    # above "Log" -- the same mechanism as the existing Mods/Options
    # divider -- so the log can be dragged taller or shorter at will instead
    # of being stuck at a fixed height.
    vsplit = ttk.PanedWindow(outer, orient='vertical')
    vsplit.pack(side='top', fill='both', expand=True, pady=(10, 0))

    content = ttk.Frame(vsplit)
    vsplit.add(content, weight=4)

    logframe = ttk.Labelframe(vsplit, text='Log', padding=(2, 2))
    vsplit.add(logframe, weight=1)

    logbox = tk.Text(logframe, height=8, wrap='none', font=('Menlo', 11),
                     relief='flat', background=LOG_BG, foreground='#c9d2e3',
                     insertbackground=ACCENT, selectbackground=BG_ROW_SEL,
                     highlightthickness=0, state='disabled')
    logscroll = ttk.Scrollbar(logframe, orient='vertical',
                              command=logbox.yview)
    logbox.configure(yscrollcommand=logscroll.set)
    logscroll.pack(side='right', fill='y')
    logbox.pack(side='left', fill='both', expand=True)

    def log_write(text):
        logbox.configure(state='normal')
        logbox.insert('end', text + '\n')
        logbox.see('end')
        logbox.configure(state='disabled')

    def log_clear():
        logbox.configure(state='normal')
        logbox.delete('1.0', 'end')
        logbox.configure(state='disabled')

    # ---------- centre: mods | options (fills remaining space) ----------
    panes = ttk.PanedWindow(content, orient='horizontal')
    panes.pack(fill='both', expand=True)

    def make_scrollable(parent, inner_width_tracks_canvas=False):
        """
        A canvas+frame scroll area that:
          - only responds to the mouse wheel when there's actually something
            to scroll (fixes being able to drag content past its own bottom
            with nothing left to show -- there was previously no bound on
            how far a wheel event could move the view);
          - shows/hides its own scrollbar depending on whether the content
            overflows, instead of always reserving the space.
        Returns (canvas, inner_frame).
        """
        canvas = tk.Canvas(parent, highlightthickness=0, bd=0,
                          background=BG_APP)
        vscroll = ttk.Scrollbar(parent, orient='vertical',
                                command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner_id = canvas.create_window((0, 0), window=inner, anchor='nw')
        canvas.configure(yscrollcommand=vscroll.set)
        canvas.pack(side='left', fill='both', expand=True)

        def sync(_evt=None):
            canvas.configure(scrollregion=canvas.bbox('all'))
            canvas.update_idletasks()
            bbox = canvas.bbox('all')
            content_h = (bbox[3] - bbox[1]) if bbox else 0
            overflow = content_h > canvas.winfo_height()
            if overflow and not vscroll.winfo_ismapped():
                vscroll.pack(side='right', fill='y')
            elif not overflow and vscroll.winfo_ismapped():
                vscroll.pack_forget()
                canvas.yview_moveto(0)

        inner.bind('<Configure>', sync)
        if inner_width_tracks_canvas:
            canvas.bind('<Configure>',
                        lambda e: (canvas.itemconfigure(inner_id,
                                                         width=e.width),
                                  sync()))
        else:
            canvas.bind('<Configure>', sync)

        def on_wheel(event):
            top, bottom = canvas.yview()
            if top <= 0.0 and bottom >= 1.0:
                return  # everything already fits -- nothing to scroll
            step = -1 if event.delta > 0 else 1
            if step < 0 and top <= 0.0:
                return
            if step > 0 and bottom >= 1.0:
                return
            canvas.yview_scroll(step, 'units')

        canvas.bind('<Enter>',
                   lambda e: canvas.bind_all('<MouseWheel>', on_wheel))
        canvas.bind('<Leave>', lambda e: canvas.unbind_all('<MouseWheel>'))
        return canvas, inner

    left = ttk.Labelframe(panes, text='Mods', padding=8)
    panes.add(left, weight=1)
    _canvas, listframe = make_scrollable(left, inner_width_tracks_canvas=True)

    # Options pane, scrollable (mods can have a dozen-plus options).
    right = ttk.Labelframe(panes, text='Options', padding=6)
    panes.add(right, weight=2)
    _opt_canvas, options_body = make_scrollable(
        right, inner_width_tracks_canvas=True)

    selected = tk.StringVar(value=mods[0].filename if mods else '')

    def archive_label(parent, archives, exact, wraplength=220, **grid_kw):
        text = build.archives_text(archives, exact)
        if not text:
            text = '—'
        lbl = ttk.Label(parent, text=text, wraplength=wraplength,
                        justify='left',
                        style='Archive.TLabel' if exact else 'ArchiveEst.TLabel')
        lbl.grid(**grid_kw)
        return lbl

    def show_options(mod):
        for w in options_body.winfo_children():
            w.destroy()
        if mod is None:
            return
        if not mod.manifest:
            ttk.Label(options_body,
                      text=(mod.error or 'Not extracted yet.') +
                      '\n\nOptions appear the first time you build.',
                      style='Sub.TLabel', justify='left').pack(anchor='w')
            return
        m = mod.manifest
        ttk.Label(options_body, text=m.name or mod.stem,
                  font=('Helvetica', 13, 'bold')).pack(anchor='w')
        meta = ' · '.join(x for x in (m.author, m.version, m.category) if x)
        if meta:
            ttk.Label(options_body, text=meta, style='Sub.TLabel'
                      ).pack(anchor='w', pady=(0, 2))
        store = settings_by_mod.setdefault(mod.filename, {})
        mod_arch, mod_exact = build.mod_archives(mod, store, catalogs)
        summary = ttk.Frame(options_body)
        summary.pack(anchor='w', pady=(2, 10))
        ttk.Label(summary, text='Modifies (as configured): ',
                  style='Sub.TLabel').pack(side='left')
        summary_text = build.archives_text(mod_arch, mod_exact) or '—'
        ttk.Label(summary, text=summary_text, wraplength=460, justify='left',
                  style='Archive.TLabel' if mod_exact else 'ArchiveEst.TLabel'
                  ).pack(side='left')
        if not mod_exact and mod_arch:
            ttk.Label(options_body,
                      text='(estimated from folder names — point this app '
                      'at your game dump for exact matches)',
                      style='Sub.TLabel', font=('Helvetica', 9)
                      ).pack(anchor='w', pady=(0, 8))
        if not m.options:
            ttk.Label(options_body, text='No configurable options.',
                      style='Sub.TLabel').pack(anchor='w')
            return
        # Fixed column widths (not content-driven) so the table stays
        # neatly aligned left-to-right instead of shifting around based on
        # whichever option happens to have the longest name; long text
        # wraps within its own column rather than stretching it.
        OPTION_COL, VALUE_COL, MODIFIES_COL = 190, 180, 200
        grid = ttk.Frame(options_body)
        grid.pack(fill='x', pady=(4, 0))
        grid.columnconfigure(0, weight=0, minsize=OPTION_COL)
        grid.columnconfigure(1, weight=0, minsize=VALUE_COL)
        grid.columnconfigure(2, weight=1, minsize=MODIFIES_COL)
        ttk.Label(grid, text='OPTION', style='ColHeader.TLabel').grid(
            row=0, column=0, sticky='w', padx=(0, 12))
        ttk.Label(grid, text='VALUE', style='ColHeader.TLabel').grid(
            row=0, column=1, sticky='w')
        ttk.Label(grid, text='MODIFIES', style='ColHeader.TLabel').grid(
            row=0, column=2, sticky='w', padx=(14, 0))
        ttk.Separator(grid, orient='horizontal').grid(
            row=1, column=0, columnspan=3, sticky='ew', pady=(2, 6))
        for i, opt in enumerate(m.options):
            row = i + 2

            # A SECTION HEADING, not a setting. Big mods punctuate their
            # option list with entries like `======Battle Menu Options======`
            # that carry a single dummy value; 7th Heaven draws them as a
            # divider. Drawn as a combobox they are seven identical
            # one-choice dropdowns with a row of equals signs for a name, and
            # Enhanced Stock UI has exactly seven of them -- which is most of
            # what "the options are screwed up" looked like.
            bare = opt.name.strip().strip('=').strip()
            if len(opt.values) <= 1 and (opt.name.strip().startswith('=')
                                         or not bare):
                ttk.Label(grid, text=bare or opt.id,
                          style='ColHeader.TLabel').grid(
                    row=row, column=0, columnspan=3, sticky='w',
                    pady=(12, 2))
                continue

            ttk.Label(grid, text=opt.name, wraplength=OPTION_COL - 16,
                     justify='left').grid(
                row=row, column=0, sticky='w', pady=3, padx=(0, 12))
            labels = [label for _, label in opt.values]
            current = store.get(opt.id, opt.default)

            # A List with no <Option> at all -- Enhanced Stock UI's
            # `controllerorder` is one. There is nothing to choose, and an
            # empty dropdown is worse than saying so.
            if not labels:
                ttk.Label(grid, text='(no choices declared)',
                          style='Sub.TLabel').grid(
                    row=row, column=1, sticky='w', pady=3, padx=(0, 14))
                store.setdefault(opt.id, current)
                continue

            var = tk.StringVar(value=opt.label_for(current))
            combo = ttk.Combobox(grid, textvariable=var, values=labels,
                                 state='readonly', width=16)
            combo.grid(row=row, column=1, sticky='ew', pady=3, padx=(0, 14))

            opt_arch, opt_exact = build.option_archives(mod, opt, catalogs)
            archive_label(grid, opt_arch, opt_exact,
                         wraplength=MODIFIES_COL - 10,
                         row=row, column=2, sticky='w', padx=(14, 0))

            def on_pick(_evt, o=opt, v=var, mf=mod.filename):
                for value, label in o.values:
                    if label == v.get():
                        settings_by_mod.setdefault(mf, {})[o.id] = value
                        break
                save_settings_now()

            combo.bind('<<ComboboxSelected>>', on_pick)
            store.setdefault(opt.id, current)

    rows = {}  # filename -> (row, indicator, checkbutton, label)

    # ---------- checkbox ----------
    # Drawn on a Canvas rather than being a tk.Checkbutton, because on macOS
    # the classic Tk checkbutton renders as a NATIVE Aqua control: the tick's
    # blue fill is the system accent, `selectcolor` does not reach it, and
    # Aqua greys every native control when its window is not frontmost. That
    # is why the ticks went flat the moment you clicked another app.
    #
    # Two rectangles and two lines are entirely ours, so they look identical
    # whatever has focus, on every platform.
    # ---------- checkbox ----------
    # Rendered by Pillow into a small image, NOT drawn with Canvas
    # primitives, and the reason is antialiasing.
    #
    # Tk's Canvas has none. A 3px corner radius on a 13px box is two or three
    # pixels of arc, so an aliased rasteriser either clips them square or
    # leaves a ragged step -- the corners simply did not read as round. And
    # `create_polygon(..., smooth=True)` is worse than it sounds: it treats the
    # points as spline CONTROL points, so it shrinks the shape and rounds the
    # straight edges too.
    #
    # Pillow supersamples 4x and downscales with LANCZOS, which gives the
    # clean macOS-shaped corner the Canvas cannot. Images are cached per
    # (state, background) -- there are only ever six -- so this costs six
    # draws for the whole window, once.
    #
    # The Canvas version is kept as a fallback for a machine without ImageTk
    # (a separate package from Pillow on Linux). It looks squarer; it is not
    # the intended appearance, just a working one.
    # _CHK_SIZE is a PLACEHOLDER. It is recomputed from the measured cap
    # height further down (`_CHK_SIZE = (_cap + 4) | 1`) before any row is
    # built, and _check_image reads it at CALL time, so the value that ends up
    # in the drawn image is the derived one. 13 is what a 9px cap produces.
    _CHK_SIZE, _CHK_RADIUS, _CHK_SS = 13, 3, 4
    # Vertical nudge for the checkbox, in pixels: positive moves it DOWN.
    # 0 is the geometrically correct value -- a square's optical centre is its
    # geometric centre, so unlike the title it needs no cap-band correction.
    # It exists because Tk's per-platform Label geometry is not something this
    # code can measure, and one named number beats hunting through pack calls.
    _CHK_NUDGE = 0
    _CHK_TICK = ((3.2, 6.8), (5.4, 9.2), (9.8, 3.6))
    _chk_cache = {}

    def _check_image(on, bg):
        key = (bool(on), bg)
        if key in _chk_cache:
            return _chk_cache[key]
        try:
            from PIL import Image, ImageDraw, ImageTk
        except Exception:
            _chk_cache[key] = None
            return None
        n, r, ss = _CHK_SIZE, _CHK_RADIUS, _CHK_SS
        S = n * ss
        im = Image.new('RGB', (S, S), bg)
        d = ImageDraw.Draw(im)
        if on:
            d.rounded_rectangle([0, 0, S - 1, S - 1], radius=r * ss,
                                fill=ACCENT)
            d.line([(x * ss, y * ss) for x, y in _CHK_TICK],
                   fill='#ffffff', width=2 * ss, joint='curve')
        else:
            d.rounded_rectangle([0, 0, S - 1, S - 1], radius=r * ss,
                                fill=bg, outline=TEXT_SECONDARY,
                                width=max(1, ss))
        img = ImageTk.PhotoImage(im.resize((n, n), Image.LANCZOS))
        _chk_cache[key] = img       # also the live reference Tk needs
        return img

    class _Check(tk.Label):
        def __init__(self, parent, variable, bg, command=None):
            # padx/pady default to 1 on a tk.Label. Left in, the widget is
            # 15px around a 13px image and any rounding in the surrounding
            # geometry lands as a visible offset -- so zero them and let the
            # image alone define the size.
            super().__init__(parent, bg=bg, bd=0, highlightthickness=0,
                             padx=0, pady=0, cursor='hand2')
            self._var = variable
            self._bg = bg
            self._command = command
            self._canvas = None
            self.bind('<Button-1>', self._toggle)
            variable.trace_add('write', lambda *_: self._draw())
            self._draw()

        def _toggle(self, _event=None):
            self._var.set(not self._var.get())
            if self._command:
                self._command()
            return 'break'

        def set_bg(self, bg):
            self._bg = bg
            self.configure(bg=bg)
            self._draw()

        def _draw(self):
            img = _check_image(self._var.get(), self._bg)
            if img is not None:
                self.configure(image=img)
                self.image = img          # belt and braces on the reference
                return
            # ---- no ImageTk: square Canvas fallback, drawn once
            if self._canvas is None:
                self._canvas = tk.Canvas(self, width=_CHK_SIZE,
                                         height=_CHK_SIZE, bd=0,
                                         highlightthickness=0, bg=self._bg)
                self._canvas.pack()
                self._canvas.bind('<Button-1>', self._toggle)
            c, n = self._canvas, _CHK_SIZE - 1
            c.configure(bg=self._bg)
            c.delete('all')
            if self._var.get():
                c.create_rectangle(0, 0, n, n, fill=ACCENT, outline=ACCENT)
                (ax, ay), (bx, by), (cx, cy) = _CHK_TICK
                c.create_line(ax, ay, bx, by, fill='#ffffff', width=2,
                              capstyle='round')
                c.create_line(bx, by, cx, cy, fill='#ffffff', width=2,
                              capstyle='round')
            else:
                c.create_rectangle(0, 0, n, n, fill=self._bg,
                                   outline=TEXT_SECONDARY)

    def _paint_row(filename, bg, fg, bold, indicator_bg):
        row, indicator, chk, label, ver = rows[filename]
        row.configure(bg=bg)
        indicator.configure(bg=indicator_bg)
        chk.set_bg(bg)
        ver.configure(bg=bg)
        label.configure(bg=bg, fg=fg,
                        font=('Helvetica', 12, 'bold' if bold else 'normal'))

    def _restyle(filename):
        if filename not in rows:
            return
        if filename == selected.get():
            _paint_row(filename, BG_ROW_SEL, ACCENT_SOFT, True, ACCENT)
        else:
            _paint_row(filename, BG_ROW, TEXT_PRIMARY, False, BG_ROW)

    def select(mod):
        prev = selected.get()
        selected.set(mod.filename)
        if prev and prev != mod.filename:
            _restyle(prev)
        _restyle(mod.filename)
        show_options(mod)

    def on_row_enter(filename):
        if filename != selected.get():
            _paint_row(filename, BG_ROW_HOVER, TEXT_PRIMARY, False, BG_ROW_HOVER)

    def on_row_leave(filename):
        if filename != selected.get():
            _restyle(filename)

    # ---------- optical vertical centring ----------
    # Tk centres a label's LINE BOX: ascent above the baseline, descent below.
    # The eye does not read that box, it reads the CAP BAND -- baseline up to
    # the top of a capital. Descent is empty space for a title like
    # "Ninostyle Chibi" with no descender, so centring the line box puts the
    # visible text BELOW centre every time. That is the "drawn a tad lower
    # than it should be", and no amount of symmetric padding fixes it because
    # the asymmetry is inside the box.
    #
    # So centre the cap band instead. With the baseline at 0 and up positive:
    #
    #     line box spans   [-descent, +ascent],  centre (ascent-descent)/2
    #     cap band spans   [0, capHeight],       centre capHeight/2
    #
    # and the correction is the difference between those two centres.
    def _ver_text(mod):
        """"v1.2.3" from the .iro manifest, or nothing if it has none."""
        m = getattr(mod, 'manifest', None)
        v = (getattr(m, 'version', '') or '').strip() if m else ''
        if not v:
            return ''
        return v if v[:1].lower() == 'v' else 'v' + v

    ROW_FONT = ('Helvetica', 12)
    _rowf = tkfont.Font(font=ROW_FONT)
    _asc = _rowf.metrics('ascent')
    _desc = _rowf.metrics('descent')

    def _cap_height(font_tuple, ascent, descent):
        """
        Ink height of a capital "A", measured at the size Tk is ACTUALLY using.

        The subtle part is units. Tk's font size 12 means 12 POINTS; Pillow's
        truetype(size=12) means 12 PIXELS. Asking Pillow for "12" therefore
        measures a different-sized glyph than Tk draws, and mixing that cap
        with Tk's ascent puts the text a pixel out -- MEASURED: cap band
        landed at y=17..25 against a row centre of 20, exactly 1px low.

        So do not trust the nominal size. Search Pillow sizes for the one
        whose ascent+descent matches Tk's linespace, and measure the cap
        there. Both numbers then describe the same rendering.
        """
        family, size = font_tuple[0], font_tuple[1]
        target = ascent + descent
        try:
            from PIL import ImageFont
            path = None
            for cand in (family, family + '.ttc', family + '.ttf',
                         '/System/Library/Fonts/Helvetica.ttc',
                         '/System/Library/Fonts/Supplemental/Arial.ttf',
                         'DejaVuSans.ttf'):
                try:
                    ImageFont.truetype(cand, 12)
                    path = cand
                    break
                except Exception:
                    continue
            if path is not None:
                best, best_err = None, None
                for px in range(max(6, size - 4), size + 10):
                    try:
                        f = ImageFont.truetype(path, px)
                    except Exception:
                        continue
                    a, d = f.getmetrics()
                    err = abs((a + d) - target)
                    if best_err is None or err < best_err:
                        best, best_err = f, err
                if best is not None:
                    box = best.getbbox('A')
                    cap = box[3] - box[1]
                    if 0.4 * target <= cap <= 1.0 * target:
                        return cap
        except Exception:
            pass
        # no Pillow, or nothing plausible: the usual cap ratio for a humanist
        # sans, applied to the size Tk reports
        return int(round(0.72 * target))

    _cap = _cap_height(ROW_FONT, _asc, _desc)

    # ---------- the cap band IS the line ----------
    # Everything vertical in a row is derived from ONE number: the ink height
    # of a capital "A". Not the line height, not ascent+descent -- those carry
    # accent space and descender space that no title uses, they vary between
    # faces, and every quantity built on them needs its own correction.
    #
    # Treat the cap band as the line and the whole problem collapses:
    #
    #   ROW_H  = cap + 2 * padding          uniform, and odd because cap is
    #   band   = the cap's centre pixel     one shared reference for everyone
    #   text   placed so its cap band lands on it
    #   box    sized from cap, centred on it
    #
    # No item needs a correction relative to any other, because none of them
    # is measured against a different yardstick. This is why the earlier
    # attempts kept moving one thing and breaking its relationship to the
    # next: they centred the label's LINE BOX, the checkbox's SQUARE and the
    # row's HEIGHT against three different measures.
    ROW_PAD = 12
    ROW_H = _cap + 2 * ROW_PAD
    ROW_H |= 1                  # odd -> the centre is a pixel, never between
    _row_mid = ROW_H // 2       # the band every item aligns to

    # The box scales with the letters rather than being a fixed 13. cap+4 is
    # the macOS proportion (a 9px cap gets a 13px box); forced odd so its
    # centre pixel exists.
    _CHK_SIZE = (_cap + 4) | 1

    # A run of n pixels has its centre pixel at top + (n-1)//2.
    _chk_y = _row_mid - (_CHK_SIZE - 1) // 2

    # Text: the cap band starts (ascent - cap) below the label's box top, so
    # put the box where that lands the cap's centre pixel on the band.
    #
    # TEXT_RASTER_TWEAK is a MEASURED constant, not a derivation, and it is
    # the one number here that is not computed from font metrics.
    #
    # The geometric model is exact -- with the cap measured correctly (9px,
    # confirmed by ROW_H coming out at 33) it predicts the cap centre landing
    # on the row centre. Screenshots of the real window say otherwise:
    #
    #     row band y=5..37 (centre 21)   cap band y=18..26 (centre 22)
    #
    # one pixel low, consistently, after the cap measurement was already
    # corrected for the points-vs-pixels mismatch. What is left is Tk's own
    # glyph rasterisation: the ink top does not sit exactly (ascent - cap)
    # below the box top once hinting rounds it, and Tk exposes nothing that
    # would let this be calculated.
    #
    # So it is calibrated. To re-derive on another platform: screenshot a row,
    # measure the row band and the cap band, and set this to
    # (row centre - cap centre).
    TEXT_RASTER_TWEAK = -1

    def _cap_band_y(font_tuple):
        """
        y for a label in `font_tuple` such that its cap band centres on the
        row's band. Same model as above, per font, so a second column in a
        different size lines up with the title by its CAPITALS rather than by
        its box.
        """
        f = tkfont.Font(font=font_tuple)
        a, d = f.metrics('ascent'), f.metrics('descent')
        cap = _cap_height(font_tuple, a, d)
        return _row_mid - ((a - cap) + (cap - 1) // 2) + TEXT_RASTER_TWEAK

    _label_y = _cap_band_y(ROW_FONT)

    # Version, right-aligned. Same size and colour as the version line in the
    # options panel, which is Sub.TLabel over the default ('Helvetica', 11).
    VER_FONT = ('Helvetica', 11)
    _ver_y = _cap_band_y(VER_FONT)
    VER_PAD_R = 10
    _verf = tkfont.Font(font=VER_FONT)
    # sized to the widest label actually present, so the title gets every
    # remaining pixel rather than a guessed reserve
    _ver_w = max([_verf.measure(_ver_text(m)) for m in mods] or [0])

    # x follows the old padding: 4px indicator, then 10, the box, then 6
    _chk_x = 4 + 10
    _label_x = _chk_x + _CHK_SIZE + 6

    for i, mod in enumerate(mods):
        # The row height is EXPLICIT, and that is what makes the title sit
        # centred. It used to be implicit: no child had a fixed height, so
        # whichever was tallest -- the label -- set the row height and then
        # centred nothing, it simply WAS the row. Any asymmetry in its text
        # box (ascent vs descent, the widget's own internal padding) showed
        # up directly as the title sitting low.
        #
        # With `pack_propagate(False)` and a fixed height, every child is
        # centred by pack inside the same cavity, so vertical placement stops
        # depending on font metrics and padding arithmetic agreeing.
        row = tk.Frame(listframe, bg=BG_ROW, highlightthickness=0, bd=0,
                       height=ROW_H)
        row.pack(fill='x', pady=(0, 4))
        row.pack_propagate(False)

        indicator = tk.Frame(row, width=4, bg=BG_ROW, highlightthickness=0,
                             bd=0)
        indicator.pack(side='left', fill='y')
        indicator.pack_propagate(False)

        mod._load_manifest()
        var = tk.BooleanVar(
            value=saved.get(mod.filename, {}).get('enabled', True))
        enabled[mod.filename] = var
        var.trace_add('write', save_settings_now)
        chk = _Check(row, var, BG_ROW)
        chk.place(x=_chk_x, y=_chk_y + _CHK_NUDGE)

        # bd/pady/highlightthickness zeroed so the label's box is exactly
        # the text, with nothing of its own to bias the centring
        label = tk.Label(row, text=mod.display_name, cursor='hand2',
                         bg=BG_ROW, fg=TEXT_PRIMARY, anchor='w',
                         font=ROW_FONT, bd=0, pady=0, padx=0,
                         highlightthickness=0)
        # relwidth 1 + a negative width gives it the rest of the row
        # leaves room for the version column on the right
        label.place(x=_label_x, y=_label_y, relwidth=1.0,
                    width=-(_label_x + _ver_w + VER_PAD_R + 12))

        ver = tk.Label(row, text=_ver_text(mod), bg=BG_ROW,
                       fg=TEXT_SECONDARY, font=VER_FONT, anchor='e',
                       bd=0, padx=0, pady=0, highlightthickness=0)
        ver.place(relx=1.0, x=-VER_PAD_R, y=_ver_y, anchor='ne')

        rows[mod.filename] = (row, indicator, chk, label, ver)

        # `ver` is in here too: it sits over part of the row, and without
        # the bindings clicking or hovering the version would do nothing and
        # the row would flicker out of its hover state as the cursor crossed
        # it.
        for widget in (row, indicator, label, ver):
            widget.bind('<Button-1>', lambda e, m=mod: select(m))
            widget.bind('<Enter>', lambda e, f=mod.filename: on_row_enter(f))
            widget.bind('<Leave>', lambda e, f=mod.filename: on_row_leave(f))

        settings_by_mod[mod.filename] = dict(
            saved.get(mod.filename, {}).get('options', {})
            or (mod.manifest.defaults() if mod.manifest else {}))

    if mods:
        select(mods[0])

    # ---------- queue pump ----------
    def pump():
        try:
            while True:
                kind, payload = messages.get_nowait()
                if kind == 'log':
                    log_write(payload)
                elif kind == 'progress':
                    i, n, label = payload
                    bar['maximum'] = max(n, 1)
                    bar['value'] = i
                    statuslabel.configure(text=label or '')
                elif kind == 'done':
                    build_btn.state(['!disabled'])
                    bar['value'] = bar['maximum'] if payload else 0
                    statuslabel.configure(text='done' if payload else 'failed')
                    if payload:
                        open_btn.state(['!disabled'])
                        # refresh options: manifests now exist post-extract
                        for m in mods:
                            m._load_manifest()
                        cur = next((m for m in mods
                                    if m.filename == selected.get()), None)
                        if cur:
                            show_options(cur)
                        messagebox.showinfo(
                            '7th Heaven NX',
                            'Build complete.\n\nCopy everything inside the '
                            'sdout folder onto the root of your SD card.')
        except queue.Empty:
            pass
        root.after(80, pump)

    def start():
        log_clear()
        build_btn.state(['disabled'])
        open_btn.state(['disabled'])
        statuslabel.configure(text='working…')
        cap_value = current_field_tex_cap()
        bg_cap_value = current_battle_bg_tex_cap()
        fbg_px_value = current_field_bg_page_px()
        os.environ[build.field_bg_repack.BUDGET_ENV] = str(
            current_field_bg_budget_mb())
        # The value IS the constant. build.py reads
        # `field_bg_dense.MAX_TRUECOLOR_PAGES` when it walks the promotion
        # ladder, so setting it here is the whole wiring -- no variable, no
        # parser, one number in one place.
        build.field_bg_dense.MAX_TRUECOLOR_PAGES = \
            current_field_bg_truecolor()
        # FINDINGS-122/123. Same wiring style as the line above: the value IS
        # the constant, set here, no variable and no parser. Settings-only for
        # now -- see _global_setting. The old single-screen key is read as a
        # fallback so a settings.json written by build 34 keeps working.
        build.field_bg_pagecap.WINDOW_HARD_CAP = bool(
            _global_setting('field_bg_window_cap',
                            _global_setting('field_bg_single_screen_cap',
                                            True)))
        build.field_bg_pagecap.FX_SPLIT = bool(
            _global_setting('field_bg_fx_split', False))
        # FINDINGS-128: refuse a compaction that breaks the frame limit.
        build.field_bg_compact.WINDOW_SAFE = bool(
            _global_setting('field_bg_compact_frame_safe', True))
        # FINDINGS-130: Cosmos ships tiles naming palettes the field has not
        # got. Harmless on FFNx, garbage on the Switch.
        build.field_bg_pagecap.CLAMP_PALETTES = bool(
            _global_setting('field_bg_clamp_palettes', True))
        os.environ[build.field_bg_repack.MAX_TOTAL_PAGES_ENV] = str(
            current_field_bg_max_pages())
        build.field_bg_repack.apply_growth_mode(
            current_field_bg_replace_only())
        # No widget yet -- settings.json only. See _global_setting.
        os.environ[build.field_bg_repack.D2_SLOT_ENV] = str(
            _global_setting('field_bg_d2_slots_per_group',
                            build.field_bg_repack.DEFAULT_D2_SLOTS_PER_GROUP))
        os.environ[build.ff7nx_marginblack.MARGIN_ENV] = str(
            _global_setting('margin_black', 0))
        os.environ[build.ff7nx_marginart.MARGIN_ART_ENV] = str(
            current_field_bg_margin_art())
        # SEVENTH_NX_BG_CLEAR and SEVENTH_NX_MOVIE_CLIP are no longer written
        # here. Both features are retired (FINDINGS-92 §6) and the GUI writing
        # a variable on every save is precisely how a retired module comes back
        # from the dead -- FINDINGS-91 §6. build.py refuses both regardless.
        os.environ[build.movie_convert.QUALITY_ENV] = current_movie_quality()
        os.environ[build.movie_convert.FIT_ENV] = current_movie_fit()
        os.environ[build.movie_convert.COLOUR_ENV] = current_movie_colour()
        os.environ[build.MOVIE_30FPS_ENV] = '1' if m30_var.get() else '0'
        os.environ[build.ANALOG_360_ENV] = '1' if a360_var.get() else '0'
        os.environ[build.NO_AUTORUN_ENV] = '1' if norun_var.get() else '0'
        os.environ[build.NO_CHEATS_ENV] = '1' if nocheat_var.get() else '0'
        os.environ[build.LIMITER_FPS_ENV] = str(current_limiter_fps())
        os.environ[build.ff7nx_ws.WIDESCREEN_ENV] = current_widescreen()
        # The FRAME HEIGHT and the MOVIE that has to meet it ride the
        # checkbox. They are one visual change and cannot be split --
        # moving the field without the movie makes the cut worse than not
        # doing either -- so they are one switch. Off means OFF even with
        # 16:9 selected.
        _ff = '1' if frame_var.get() else '0'
        os.environ[build.FIELD_FRAME_ENV] = _ff
        os.environ[build.MOVIE_ALIGN_ENV] = _ff
        # The MODEL CULL is NOT on it and has no switch of its own. Its box
        # is 4:3-sized; against a 16:9 frame that is simply a bug, and there
        # is no configuration in which you would want an NPC switched off
        # while still inside the picture. It follows the 16:9 setting and
        # nothing else. SEVENTH_NX_MODEL_CULL exists for a diagnostic A/B
        # and is deliberately NOT written here, so it stays unset and the
        # module falls through to ff7nx_ws.enabled().
        os.environ.pop(build.MODEL_CULL_ENV, None)
        # The BATTLE OVERLAYS and the SWIRL SCALE are on the same footing as
        # the model cull and get no switch either.  Every value they write is
        # a widescreen value -- x -107, w 854, h 480, and a 4/3 vertex scale --
        # so at 4:3 they are not a milder version of themselves, they are
        # wrong.  There is no configuration in which you want the battle fade
        # to cover the middle 4:3 of a 16:9 frame.  They follow ff7nx_ws and
        # nothing else; the two env vars exist for a diagnostic A/B and are
        # deliberately NOT written here.
        os.environ.pop(build.BATTLE_WIDE_ENV, None)
        os.environ.pop(build.SWIRL_SCALE_ENV, None)
        # The 2D VIEWPORT CLIP is the same case again, and the STRONGEST of
        # them. At 4:3 the geometry and the window clip are both on the
        # unscaled 2x mapping, so they agree and the clip is doing its real
        # job -- clipping the contents FF7 stages off-screen and slides in.
        # This patch removes that clip, so at 4:3 it is not "milder", it is
        # WRONG. It must follow 16:9 and nothing else. FINDINGS-103.
        os.environ.pop(build.UI_CLIP_ENV, None)
        # The CREDITS FADE QUAD is the same case: -107 and 747 are
        # widescreen values, and at 4:3 the visible span IS 0..640, so
        # widening the quad would drag it off both edges of a frame that
        # was never narrowed. Follows 16:9, no switch. HANDOFF-104.
        os.environ.pop(build.CREDITS_ENV, None)
        os.environ[build.ff7nx_fieldbuf.SCALE_ENV] = str(
            current_field_buffer())
        os.environ[build.ff7nx_shaders.SCALER_ENV] = current_scaler()
        os.environ[build.ff7nx_shaders.FXAA_ENV] = current_fxaa()
        os.environ[build.ff7nx_shaders.VIDEO_ENV] = \
            current_video_shader()
        build.save_settings(SETTINGS, snapshot_settings())
        os.environ[build.FIELD_TEX_CAP_ENV] = str(cap_value)
        if cap_value:
            log_write(f'field texture cap: {cap_value}px '
                      '(char.lgp/world_us.lgp model textures larger than '
                      'this will be downscaled)')
        os.environ[build.BATTLE_BG_TEX_CAP_ENV] = str(bg_cap_value)
        if bg_cap_value != 256:
            log_write(f'Arisen battle background texture cap: '
                      f'{bg_cap_value}px (only tiles from Avalanche '
                      'Arisen -- everything else in battle.lgp stays at '
                      'the proven 256px)')
        os.environ[build.ff7nx_fieldbg.PAGE_PX_ENV] = str(fbg_px_value)
        if fbg_px_value != build.ff7nx_fieldbg.OFF_PAGE_PX:
            kb = fbg_px_value * fbg_px_value * 2 // 1024
            per = build.ff7nx_fieldbg.page_cost_bytes(fbg_px_value) / 1048576.0
            log_write(f'field background page size: {fbg_px_value}px for '
                      f'TRUECOLOR pages ({kb} KB of pixels each, '
                      f'{per:.2f} MB once the engine builds its 32bpp '
                      f'surface). 8-bit paletted pages stay 256px -- see '
                      'README-field-bg-512-MEASURED.md.')
            log_write(f'    the heaviest field in the game has 12 pages, so '
                      f'the worst case is {12 * per:.2f} MB against the '
                      f'3.75 MB it costs as vanilla paletted pages.')
            if fbg_px_value == build.ff7nx_fieldbg.VANILLA_PAGE_PX:
                log_write('    256px needs NO module patch -- every word it '
                          'would write already holds 256 -- so this setting '
                          'travels in flevel.lgp alone and can be tested '
                          'without a game dump.')
            else:
                log_write('    this patches exefs/main AND rewrites '
                          'flevel.lgp to match; both halves are needed, so '
                          'do not mix an flevel from one setting with a '
                          'module from another.')
            _tc = current_field_bg_truecolor()
            log_write('    truecolor: up to %d page(s) per field may be '
                      'promoted from 8-bit paletted to 16-bit (3 is what '
                      'shipped).' % _tc if _tc else
                      '    truecolor: OFF -- no page is promoted; every page '
                      'stays 8-bit paletted.')

        def worker():
            ok = False
            try:
                ok = run_build(
                    mods,
                    {k: v.get() for k, v in enabled.items()},
                    settings_by_mod,
                    lambda s: messages.put(('log', s)),
                    lambda i, n, label='': messages.put(
                        ('progress', (i, n, label))),
                    fps_60=bool(fps_var.get()))
            except Exception as exc:
                import traceback
                messages.put(('log', 'ERROR: ' + str(exc)))
                messages.put(('log', traceback.format_exc()))
            finally:
                messages.put(('done', ok))

        threading.Thread(target=worker, daemon=True).start()

    build_btn.configure(command=start)
    pump()

    # The UI is fully built from here on, so trace callbacks are meaningful.
    _ui_ready[0] = True

    def on_close():
        save_settings_now()
        root.destroy()

    root.protocol('WM_DELETE_WINDOW', on_close)

    if not mods:
        log_write(f'No .iro files found in {MODS_DIR}')
        log_write('Drop your 7th Heaven mods there and restart.')
    else:
        log_write('Ready. Tick the mods you want, choose options, '
                  'then Build SD Output.')
        log_write('If a heavily-modded scene crashes on load, try the '
                  '"Field texture cap" dropdown (top right) before '
                  'disabling mods -- it downscales oversized character/'
                  'world model textures without changing anything else.')

    root.mainloop()


def main():
    if '--cli' in sys.argv:
        mods = discover_mods()
        saved = build.load_settings(SETTINGS)
        if build.FIELD_TEX_CAP_ENV not in os.environ:
            cap_value = saved.get('__global__', {}).get('field_tex_cap', 0)
            os.environ[build.FIELD_TEX_CAP_ENV] = str(cap_value)
        if build.BATTLE_BG_TEX_CAP_ENV not in os.environ:
            bg_cap_value = saved.get('__global__', {}).get(
                'battle_bg_tex_cap', 256)
            os.environ[build.BATTLE_BG_TEX_CAP_ENV] = str(bg_cap_value)
        if build.ff7nx_fieldbg.PAGE_PX_ENV not in os.environ:
            _g = saved.get('__global__', {})
            _px = _g.get('field_bg_page_px', build.ff7nx_fieldbg.OFF_PAGE_PX)
            # Same migration as the dialog: before the ladder was split, 256
            # WAS "off". Reading it back literally would switch the repack on
            # for a headless build that had it off.
            if _px == 256 and not _g.get('field_bg_ladder_v2'):
                _px = build.ff7nx_fieldbg.OFF_PAGE_PX
            os.environ[build.ff7nx_fieldbg.PAGE_PX_ENV] = str(_px)
        if build.field_bg_repack.BUDGET_ENV not in os.environ:
            os.environ[build.field_bg_repack.BUDGET_ENV] = str(
                saved.get('__global__', {}).get(
                    'field_bg_budget_mb',
                    build.field_bg_repack.DEFAULT_BUDGET_MB))
        # Truecolor pages per field. The saved value IS
        # `field_bg_dense.MAX_TRUECOLOR_PAGES`; the old PARTIAL_ENV it used
        # to write fed a code path that stopped being called.
        build.field_bg_dense.MAX_TRUECOLOR_PAGES = int(
            saved.get('__global__', {}).get(
                'field_bg_truecolor_pages',
                build.field_bg_dense.MAX_TRUECOLOR_PAGES))
        # FINDINGS-122/123: the hard 256-tile cap, measured per camera window.
        # On by default; the switch exists so it can be A/B'd against a build
        # without it rather than argued about. The fx-byte split is a separate
        # switch and is OFF -- see the note in field_bg_pagecap.
        _sg = saved.get('__global__', {})
        build.field_bg_pagecap.WINDOW_HARD_CAP = bool(
            _sg.get('field_bg_window_cap',
                    _sg.get('field_bg_single_screen_cap', True)))
        build.field_bg_pagecap.FX_SPLIT = bool(
            _sg.get('field_bg_fx_split', False))
        build.field_bg_compact.WINDOW_SAFE = bool(
            _sg.get('field_bg_compact_frame_safe', True))
        build.field_bg_pagecap.CLAMP_PALETTES = bool(
            _sg.get('field_bg_clamp_palettes', True))
        if build.field_bg_repack.REPLACE_ONLY_ENV not in os.environ:
            build.field_bg_repack.apply_growth_mode(
                saved.get('__global__', {}).get('field_bg_replace_only', 2))
        if build.field_bg_repack.D2_SLOT_ENV not in os.environ:
            os.environ[build.field_bg_repack.D2_SLOT_ENV] = str(
                saved.get('__global__', {}).get(
                    'field_bg_d2_slots_per_group',
                    build.field_bg_repack.DEFAULT_D2_SLOTS_PER_GROUP))
        if build.ff7nx_marginblack.MARGIN_ENV not in os.environ:
            os.environ[build.ff7nx_marginblack.MARGIN_ENV] = str(
                saved.get('__global__', {}).get('margin_black', 0))
        if build.ff7nx_marginart.MARGIN_ART_ENV not in os.environ:
            os.environ[build.ff7nx_marginart.MARGIN_ART_ENV] = str(
                saved.get('__global__', {}).get('margin_art', 0))
        # bg_clear / movie_clip: retired, no longer defaulted from settings.
        #
        # The field-frame defaults used to be nested INSIDE the movie_clip
        # branch, so they were only ever applied when SEVENTH_NX_MOVIE_CLIP
        # happened to be unset. Un-nested here; `setdefault` means an explicit
        # environment override still wins.
        _ff = str(int(bool(saved.get('__global__', {})
                           .get('field_frame', 1))))
        os.environ.setdefault(build.FIELD_FRAME_ENV, _ff)
        os.environ.setdefault(build.MOVIE_ALIGN_ENV, _ff)
        # SEVENTH_NX_MODEL_CULL and SEVENTH_NX_MOVIE_BARS are deliberately left
        # unset: the cull follows 16:9 rather than this checkbox, and the FMV
        # margin bars follow MOVIE_ALIGN_ENV, which is set just above.
        # SEVENTH_NX_BATTLE_WIDE, SEVENTH_NX_SWIRL_SCALE,
        # SEVENTH_NX_UI_CLIP and SEVENTH_NX_CREDITS likewise follow 16:9 --
        # see the dialog for why they have no switch.
        if build.field_bg_repack.MAX_TOTAL_PAGES_ENV not in os.environ:
            os.environ[build.field_bg_repack.MAX_TOTAL_PAGES_ENV] = str(
                saved.get('__global__', {}).get(
                    'field_bg_max_pages',
                    build.field_bg_repack.DEFAULT_MAX_TOTAL_PAGES))
        if build.ff7nx_ws.WIDESCREEN_ENV not in os.environ:
            # Same rule as the dialog: a saved legacy value is a known-bad
            # build and reads back as Off. A headless run must not silently
            # ship `field` because settings.json predates this cleanup.
            _ws = saved.get('__global__', {}).get('widescreen', '')
            if _ws == 'ws-2d':
                _ws = build.ff7nx_ws.MODE_WS_3D
            if _ws is True or _ws is False or _ws in build.ff7nx_ws.LEGACY_MODES:
                _ws = ''
            os.environ[build.ff7nx_ws.WIDESCREEN_ENV] = str(_ws)
        if build.ff7nx_fieldbuf.SCALE_ENV not in os.environ:
            _fb = saved.get('__global__', {}).get(
                'field_buffer', build.ff7nx_fieldbuf.DEFAULT_SCALE)
            try:
                _fb = int(_fb)
            except (TypeError, ValueError):
                _fb = build.ff7nx_fieldbuf.DEFAULT_SCALE
            os.environ[build.ff7nx_fieldbuf.SCALE_ENV] = str(_fb)
        if build.ff7nx_shaders.SCALER_ENV not in os.environ:
            os.environ[build.ff7nx_shaders.SCALER_ENV] = str(
                saved.get('__global__', {}).get('scaler', '') or '')
        if build.ff7nx_shaders.FXAA_ENV not in os.environ:
            os.environ[build.ff7nx_shaders.FXAA_ENV] = str(
                saved.get('__global__', {}).get('fxaa', '') or '')
        if build.ff7nx_shaders.VIDEO_ENV not in os.environ:
            os.environ[build.ff7nx_shaders.VIDEO_ENV] = str(
                saved.get('__global__', {}).get('video_shader', '') or '')
        if build.MOVIE_30FPS_ENV not in os.environ:
            os.environ[build.MOVIE_30FPS_ENV] = \
                '1' if saved.get('__global__', {}).get('movie_30fps') else '0'
        os.environ[build.ANALOG_360_ENV] = (
                '1' if saved.get('__global__', {}).get('analog_360') else '0')
        os.environ[build.NO_AUTORUN_ENV] = (
                '1' if saved.get('__global__', {}).get('no_autorun') else '0')
        os.environ[build.NO_CHEATS_ENV] = (
                '1' if saved.get('__global__', {}).get('no_cheats') else '0')
        os.environ[build.LIMITER_FPS_ENV] = str(
                saved.get('__global__', {}).get('limiter_fps', 0))
        if build.movie_convert.FIT_ENV not in os.environ:
            os.environ[build.movie_convert.FIT_ENV] = \
                saved.get('__global__', {}).get(
                    'movie_fit', build.movie_convert.FIT_DEFAULT)
        if build.movie_convert.COLOUR_ENV not in os.environ:
            os.environ[build.movie_convert.COLOUR_ENV] = \
                saved.get('__global__', {}).get(
                    'movie_colour', build.movie_convert.COLOUR_DEFAULT)
        if build.movie_convert.QUALITY_ENV not in os.environ:
            os.environ[build.movie_convert.QUALITY_ENV] = \
                saved.get('__global__', {}).get(
                    'movie_quality', build.movie_convert.QUALITY_DEFAULT)
        enabled = {m.filename: saved.get(m.filename, {}).get('enabled', True)
                   for m in mods}
        settings = {}
        for m in mods:
            m.ensure_extracted(print)
            settings[m.filename] = (saved.get(m.filename, {}).get('options')
                                    or (m.manifest.defaults()
                                        if m.manifest else {}))
        # --60fps / --no-60fps override the saved setting for this run.
        fps_60 = saved.get('__global__', {}).get('fps_60', False)
        if '--60fps' in sys.argv:
            fps_60 = True
        if '--no-60fps' in sys.argv:
            fps_60 = False
        ok = run_build(mods, enabled, settings, print,
                       lambda *a: None, fps_60=fps_60)
        return 0 if ok else 1
    launch_ui()
    return 0


if __name__ == '__main__':
    sys.exit(main())