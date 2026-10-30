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

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'src'))
import nxpaths  # noqa: E402  BUILD 755: the code lives in src/<area>/
nxpaths.install()
import ff7nx_deps  # noqa: E402
# Installs missing packages on first run (possibly re-running this script in
# a private .venv), so this has to come before any third-party import.
ff7nx_deps.ensure(nxpaths.ROOT, _os.path.abspath(__file__),
                  gui='--cli' not in _sys.argv)
import json
import os
import queue
import re
import sys
import threading
import weakref


# The user's folder (mods, cache, sdout, settings). The program's own files
# (assets/, data/) are under nxpaths.APP, which is the same folder unless
# this is a release build.
HERE = nxpaths.ROOT

# Shown in the header, nearest the title. Single source of truth --
# anything else that needs it should import this rather than repeat it.
VERSION = '1.0.4'
sys.path.insert(0, HERE)

import build  # noqa: E402
import build_guard  # noqa: E402
import ff7nx_esui  # noqa: E402
import iro  # noqa: E402
import lgp    # noqa: E402
import ff7nx_field169  # noqa: E402
import ff7nx_modifies  # noqa: E402
import nxsettings  # noqa: E402  BUILD 756: user vs developer settings
import nxthemes  # noqa: E402

# Resolved once, at import: $SEVENTH_NX_DUMP, then dump/, then the old
# workingdir/, then any subfolder that contains romfs/ff7/workingdir.
DUMP = build.find_game_dump(HERE)
WORKINGDIR = DUMP.workingdir if DUMP else os.path.join(HERE, 'workingdir')
MODS_DIR = os.path.join(HERE, 'mods')
CACHE_DIR = os.path.join(HERE, 'cache')
SDOUT_DIR = os.path.join(HERE, 'sdout')
SETTINGS = os.path.join(HERE, 'settings.json')
# Factory settings: Settings > Restore defaults, and the starting value of
# any __global__ key settings.json does not have yet.
DEFAULT_SETTINGS = os.path.join(nxpaths.APP, 'data', 'default_settings.json')
# Factory mod settings: Install Mods > Restore defaults, and the starting
# state of a supported mod that settings.json does not list yet.
DEFAULT_MOD_SETTINGS = os.path.join(nxpaths.APP, 'data', 'default_mod_settings.json')
# BUILD 756/804. Developer mode shows every setting; without it only the user
# settings are shown. Either way the build uses what settings.json holds
# (nxsettings.LOCKED only fills keys it does not have yet). On with
# SEVENTH_NX_DEV=1, or with the saved Settings > GUI > Developer Settings
# toggle. DEV_MODE is the value at startup; the dialog asks
# nxsettings.dev_mode() because the toggle can change it while running.


def _saved_dev_flag():
    try:
        with open(SETTINGS) as fh:
            return bool(json.load(fh).get('__global__', {}).get(
                'developer_settings', False))
    except Exception:                                           # noqa: BLE001
        return False


nxsettings.set_dev_override(_saved_dev_flag())
DEV_MODE = nxsettings.dev_mode()


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
    # BUILD 806: no settings.json yet (a fresh install) still means the
    # factory values, not `default` -- several callers pass a default that
    # disagrees with data/default_settings.json, and the first save wrote
    # that disagreement to disk, so Developer Settings read "Modified (3)"
    # on a copy nobody had touched.
    try:
        with open(SETTINGS) as fh:
            saved = json.load(fh).get('__global__', {})
    except Exception:                                           # noqa: BLE001
        saved = {}
    try:
        return nxsettings.effective_global(dict(
            default_globals(), **saved)).get(key, default)
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
# First match wins. BUILD 831: the supported mods always take the tested
# order of data/mod_sources.json (the Install Mods and README order), however
# they were installed; other mods follow in the category auto-sort (see
# apply_reference_order). The saved order no longer decides it: with no drag
# or Auto-sort here, a mod installed last could never leave the bottom.
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

# World-map texture dimension cap -- see build.WORLD_TEX_CAP_ENV for the
# measurements. Unlike the field cap this defaults ON, at 512px, because
# vanilla world_us.lgp tops out at 256px and nothing above that has ever
# been through this port's world texture bind. It is NOT a memory limit:
# ff7nx_heap raises the guest heap to 256 MB on every build, and a paletted
# world set is under 10% of that at 512px.
WORLD_TEX_CAP_CHOICES = [
    (512, 'Cap at 512px — recommended'),
    (1024, 'Cap at 1024px'),
    (768, 'Cap at 768px'),
    (256, 'Cap at 256px — vanilla’s own maximum'),
    (128, 'Cap at 128px'),
    (0, 'Off — full size, as shipped'),
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

# Spell/summon texture cap -- see build.ff7nx_spelltex. Scoped to magic.lgp
# textures rebuilt from an FFNx DDS set (SYW Unified Spell Textures, and
# Enhanced Stock UI's battle cursor). Nothing else in magic.lgp moves.
#
# MEASURED, so the ladder means something: SYW authored every file at
# EXACTLY 4x its vanilla slot (110 of 120 sampled), and vanilla magic.lgp
# tops out at 256px, so this is really a uniform whole-number scale --
# 512 = 2x, 768 = 3x, 1024 = 4x, same shape as the Gaia world cap.
#
# "Native" is the zero-risk end of the ladder and it is NOT the default,
# because it is also the pointless end: the art is rebuilt at the vanilla
# 128px/256px slot size, so the mod is applied correctly and you can barely
# see it. 1,013 of magic.lgp's 1,109 textures are locked to 16 colours per
# palette, so resolution is the only place the detail can go.
#
# The honest caveat: vanilla magic.lgp is the ONE archive this project has
# never pushed past 256px, and ff7nx_battlecap records that every archive
# that was pushed past it has been reported corrupting. This setting is how
# that gets tested one step at a time, and the mod's own toggle is the
# revert.
SPELL_TEX_CAP_CHOICES = [
    (512, '512px \u2014 2x (default)'),
    (768, '768px \u2014 3x'),
    (1024, '1024px \u2014 4x'),
    (0, 'Vanilla size \u2014 no upscaling'),
]

# SYW minigame DDS conversion has its own native TEX path. Keep its cap
# separate from field, world and spell art so changing one does not rebuild
# unrelated archives. This is an upper bound on each integer upscale, not a
# promise to enlarge every texture to the selected dimension.
MINIGAME_TEX_CAP_CHOICES = [
    (256, '256px — conservative default'),
    (512, '512px'),
    (768, '768px'),
    (1024, '1024px'),
    (0, 'Native size — no upscaling'),
]

# Retained only so older settings.json files load. V37 uses the single spell
# cap above for every mapped TEX; the per-TEX runtime marker replaces this
# split. Production conversion ignores the old value.
SPELL_FX_CAP_CHOICES = [
    (0, 'Vanilla size \u2014 known good (default)'),
    (512, '512px \u2014 2x, EXPERIMENTAL'),
    (768, '768px \u2014 3x, EXPERIMENTAL'),
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
    # BUILD 859 (user, 10-29: "right at 256px and not right at 512px may be
    # misleading and stale"). Labelled for 768px pages, the default page
    # size, where one truecolor page is about 3.5 MB of runtime memory; the
    # old 256px/512px page counts and hardware notes were from builds before
    # 768px pages and are in docs/notes/SETTINGS-HELP-ARCHIVE-755.md.
    (0.0,  'Unlimited \u2014 no budget (default)'),
    (3.0,  '3.0 MB \u2014 no 768px page fits'),
    (4.0,  '4.0 MB \u2014 one 768px page'),
    (4.5,  '4.5 MB \u2014 one 768px page, a little headroom'),
    (5.0,  '5.0 MB \u2014 one 768px page'),
    (5.5,  '5.5 MB \u2014 one 768px page (the old default)'),
    (5.75, '5.75 MB \u2014 one 768px page'),
    (6.0,  '6.0 MB \u2014 one 768px page'),
    (7.5,  '7.5 MB \u2014 two 768px pages'),
    (9.0,  '9.0 MB \u2014 two 768px pages'),
    (11.0, '11.0 MB \u2014 three 768px pages'),
    (14.0, '14.0 MB \u2014 three 768px pages'),
    (18.0, '18.0 MB \u2014 five 768px pages'),
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
# NOT A HARD CEILING -- and this paragraph used to claim it was, two lines
# above another that called 4..7 "the probe". Both cannot be true. What holds
# is: the opaque truecolor band is slots 26, 27, 28 (field_bg_native.
# D2_OPAQUE_SLOTS -- every depth-2 page in the vanilla archive sits in one of
# those three) and slot 29+ never renders. The count above 3 is served from
# FREE LOW SLOTS instead, which do render, so values above 3 ARE honoured.
# 4..7 ARE THE PROBE. FINDINGS-141.
#
# The old list stopped at 3 and its label said "the opaque band is 3 slots
# wide". That is `field_bg_native.D2_OPAQUE_SLOTS`, and the reason it is 3 is
# `field_bg_repack.DEFAULT_D2_SLOTS_PER_GROUP`: "29 does not allocate,
# measured on mds6_2". That is an ALLOCATION failure -- `field_load_textures`
# makes one texture per present page and aborts the whole loop on the first
# one it cannot serve -- and an allocation failure is a function of HEAP SIZE.
# This build raises FF7's heap from the port's hardcoded 64 MB to 256 MB
# (`ff7nx_heap.HEAP_MB`). THAT EXPLANATION WAS WRONG AND IS RETRACTED: build
# 52 reproduced the black squares WITH the 256 MB heap, and neither build
# crashed -- an allocation failure would. The page simply never becomes a
# texture at slot 29+. It is placement, not heap size.
#
# The engine is not the limit: `field_init_bg_pages` (+0x92D048) does ONE
# test, `cmp w28, #0x1a; cset lo`, so slots 0x1A..0x29 are ALL depth-2 --
# sixteen, pre-allocated whether or not the archive fills them.
#
# WHY THIS LIST HAD TO CHANGE AT ALL: a value not in it is silently replaced
# by `MAX_TRUECOLOR_PAGES` when the GUI loads, so setting 4 in settings.json
# by hand snapped straight back to 3 and the build would have been a no-op.
# Three separate gates hold this number down -- this list, the dropdown's
# value, and `d2_slots_per_group()` -- and all three have to agree.
#
# 4 is the probe: one slot past the wall, so a failure is small and specific.
# 7 is where it should land if 4 works, because the dense repack wants 6.18
# pages per field and is currently given 2.61.
FIELD_BG_TRUECOLOR_CHOICES = [
    # THREE. PROVEN ON HARDWARE TWICE, AGAINST THE DISASSEMBLY. FINDINGS-144.
    #
    # The original x86 this port recompiles (md5 ca7284c3..., identical to
    # ff7_en_switch) permits far more, and I read all three functions in the
    # path:
    #     field_load_textures       0x640292  loop i < 0x2a, type-2 i < 0x21
    #                                         -> blend 4 (opaque)
    #     field_convert_type2_layers 0x63F385  loop i < 0x2a
    #     read_field_background_data 0x62B6F1  loop i < 0x2a
    # All three cover slots 0..41. By that reading slots 26..32 are opaque
    # truecolor and seven pages should work.
    #
    # THE PORT DISAGREES, AND HARDWARE IS THE AUTHORITY:
    #     build 52  slot 29 only        -> black squares
    #     build 55  slots 29, 30, 31    -> black squares, 124 fields
    # No crash either time, which rules out an allocation failure -- the page
    # simply never becomes a texture. MEASURED on the build-55 archive, the
    # DATA is fine: black-cell rate 4.41% on slots 26-28 and 4.85% on 29+,
    # i.e. identical and all of it genuine dark art. So the archive is right
    # and the divergence is in the recompiled port, not in what we write.
    #
    # Do not raise this again without runtime evidence from the port itself.
    # Two builds and a full read of the x86 were not enough to predict it.
    # STALE LABEL CORRECTED. It read "the only value that renders on this
    # port", which is not what was measured and is contradicted by the builds
    # since: this is a BASE cap, and field_bg_dense's LOW-SLOT PROBE lifts it
    # to LOW_SLOT_MAX_TC wherever the field has free slots below 26. MEASURED
    # on the shipped archive: 117 field(s) hold 4 truecolor pages, 35 hold 5,
    # 8 hold 6 and 2 hold 7, with no black squares reported.
    #
    # What was actually measured is PLACEMENT, not count: slots 29+ never
    # become textures on this port (builds 52 and 55, black squares, no
    # crash). Free LOW slots draw correctly, which is why the probe exists.
    (7, '7 pages per field — slots 26..32, needs the loader-bound patch'),
    (3, '3 pages per field \u2014 base cap; free low slots raise it further'),
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
    # THE "~13%" HERE WAS WRONG. That figure is `replace_only` mode (option 1
    # of the control below), and these presets set replace_only = 2, so it
    # never applied to them. MEASURED on the current archive, 702 fields:
    # 69.9% of tiles draw from a truecolor page. The share moves every build,
    # so the label no longer quotes one -- read DENSE REPACK in the log.
    (2, 'Sharper but MIXED — 384px truecolor pages beside 256px paletted'),
    (3, 'Sharpest but MIXED — 512px truecolor pages beside 256px paletted'),
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
# PALETTED (depth-1) PAGE SIZE. This is the fx-texture size cap.
#
# It used to be settings.json-only, on the argument that a dropdown "would
# imply a freedom it does not have" because the size is an ARCHIVE FORMAT
# change needing ten matching module words. That argument was wrong in the
# way that matters: the freedom is real -- `build.py` emits the module words
# from this value and asserts if they disagree -- and hiding the control did
# not make the setting safer, it made it invisible. The user asked for it
# twice.
#
# IT IS THE ONLY CONTROL OVER FX TEXTURE SIZE THERE CAN BE. FINDINGS-225:
# an fx page is an ordinary page bound at tile offset 34, it is almost always
# depth-1, and section 9 has no per-page size field -- the engine infers the
# dimension from the patched constant. So every depth-1 page in a build is
# the same size and "cap the fx pages only" is not expressible in the format.
# This knob moves all of them together, which is the whole available lever.
#
# MEASURED on the shipped 512px build, all 711 fields:
#     depth-1 pages   1,609    421.8 MB     of which 813 are fx: 213.1 MB
#     heaviest field  md_e1    5,632 KB across 17 pages
# At 256 the depth-1 term drops to 105 MB and uutai1 goes 4,096 -> 2,048 KB.
FIELD_BG_D1_PX_CHOICES = [
    (256, '256px — vanilla size, what the game shipped (safest)'),
    (512, '512px — 4x the bytes; needed for sharp fx/paletted art'),
]

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
    (name, label)
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
# The framing stage is the recommended `ws-3d` dropdown entry now. Plain
# `ws` remains as a data-only diagnostic, and SEVENTH_NX_WS_FRAMING=1 can
# still force framing on top of it for scripted measurements. The labels
# used to call the DATA-ONLY entry "16:9 -- recommended" and the real one
# "+ wide frame". That was true when the framing stage was unproven; it is
# not any more, and it sent a build to the wrong stage: `ws` bakes camera
# ranges and never opens exefs/main, so the console still shows 4:3 and
# nothing in the log said why. The label now says what each one DOES.
WIDESCREEN_CHOICES = [
    ('', '4:3 \u2014 original'),
    ('ws', 'Data only \u2014 camera ranges, picture stays 4:3 (developer)'),
    ('ws-3d', '16:9 widescreen (recommended)'),
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
    (1, '1\u00d7 \u2014 428\u00d7240, no bands'),
    (2, '2\u00d7 \u2014 854\u00d7480, no bands, sharper field'),
    (3, '3\u00d7 \u2014 1280\u00d7720, no bands, exactly 16:9, field at '
        'native 720p'),
    # BUILD 757: untested on hardware. 47.6 / 60.9 MB more than stock out
    # of the 256 MB driver pool; see ff7nx_fieldbuf.
    (4, '4\u00d7 \u2014 1706\u00d7960, no bands (experimental)'),
    (4.5, '4.5\u00d7 \u2014 1920\u00d71080 (tested on hardware)'),
    # BUILD 777: every 768px-page texel on exactly 2 buffer px (4.5 gives
    # 1.5, which shimmers on straight edges while the camera moves); the
    # 1440-row buffer is reduced to the screen. 112.5 MB of targets.
    # BUILD 788: the default, confirmed on hardware at 60 fps in fship_2.
    (6, '6\u00d7 \u2014 2560\u00d71440, downscaled to the screen '
        '(recommended)'),
]
FIELD_BUF_LABELS = dict(FIELD_BUF_CHOICES)
FIELD_BUF_BY_LABEL = {v: k for k, v in FIELD_BUF_CHOICES}

# The GRAPHICS pool -- nv::InitializeGraphics at module +0x113BD7C.
#
# Read the note above on the field render target: "out of the same pool the
# field background PAGES allocate from". THIS is that pool, and until now
# nothing in this build had a way to make it bigger. It is not the guest
# heap; the heap holds FF7's own pixel buffers and is raised separately by
# ff7nx_heap. This one is the GL driver's, and it holds the textures, the
# render targets, the vertex buffers and the command memory.
#
# 256 MB is what the port shipped. What this build asks of it that the
# stock game did not: +25.78 MB of permanent field render target at 3x,
# 768px truecolor field pages at 3.38 MB apiece against vanilla's 0.31 MB,
# a 768px world texture cap, a 512px char cap and a 768px battle background
# cap.
#
# See FINDINGS-302 for what the crash reports do and do not establish.
GFX_POOL_CHOICES = [
    (128, '128 MB — −128  ⚗ diagnostic: may not boot'),
    (160, '160 MB — −96   ⚗ diagnostic: may not boot'),
    (192, '192 MB — −64   ⚗ diagnostic: THE used-extent A/B'),
    (224, '224 MB — −32   ⚗ diagnostic'),
    (256, '256 MB — stock. LEAVE IT HERE (raising it corrupts textures)'),
    (320, '320 MB — +64   ⚠ causes texture corruption'),
    (384, '384 MB — +128  ⚠ causes texture corruption'),
    (448, '448 MB — +192  ⚠ causes texture corruption'),
    (512, '512 MB — +256  ⚠ causes texture corruption'),
]
GFX_POOL_LABELS = dict(GFX_POOL_CHOICES)
GFX_POOL_BY_LABEL = {v: k for k, v in GFX_POOL_CHOICES}
# Spelled out here rather than imported at module scope so this file keeps
# importing cleanly on a machine where the project modules are not on the
# path yet; the two places that use it check it against ff7nx_gfxpool.
_HEADLESS_GFX_ENV = 'SEVENTH_NX_GFX_POOL_MB'
# Same reason as above: spelled out rather than imported at module scope, and
# checked against ff7nx_texcache.MODE_ENV by test_texcache.py.
_HEADLESS_TEXCACHE_ENV = 'SEVENTH_NX_TEX_CACHE'

# Freeing a texture on this port parks the surface in a table keyed on its
# (width, height), ten per size, and only a later texture of exactly those
# dimensions ever takes it back out. Nothing else empties it. 137.6 MB of
# corpses on this build against 16.5 MB in vanilla, out of a 256 MB pool --
# that was the FPS sag and the texture corruption. FINDINGS-306.
TEX_CACHE_CHOICES = [
    ('small', 'Bounded small — ≤256px, 10/size, 64 total (recommended)'),
    ('nocache', 'Off — no recycling at all (confirmed fix, costs FPS)'),
    ('off', 'Stock — ⚠ leaks 137 MB, corrupts textures'),
]
TEX_CACHE_LABELS = dict(TEX_CACHE_CHOICES)
TEX_CACHE_BY_LABEL = {v: k for k, v in TEX_CACHE_CHOICES}

# The background scaler and the full-screen AA. Both are PIXEL shaders the
# port already loads from romfs; these are drop-in replacements that used to
# have to be copied onto the card by hand. See ff7nx_shaders.py.
VOICE_NORM_CHOICES = [
    ('speech:-16.2', 'Even (recommended)'),
    ('speech:-15.2', 'Even, louder'),
    ('speech:-17.2', 'Even, quieter'),
    ('loudness:-15.3', 'Broadband loudness \u2014 pre-349 (developer)'),
    ('peak:-1.1', 'Equal peaks only, \u22121.1 dBFS (developer)'),
    ('off', 'Off \u2014 as the mod shipped'),
]
VOICE_NORM_LABELS = dict(VOICE_NORM_CHOICES)
VOICE_NORM_BY_LABEL = {v: k for k, v in VOICE_NORM_CHOICES}

# BUILD 815: HD lite is the shipped default (data/default_settings.json), so
# it is the recommended entry and sits second; full HD follows it.
SCALER_CHOICES = [
    ('', 'Original'),
    ('hd_lite', 'HD lite (recommended)'),
    ('hd', 'HD \u2014 full hq4x'),
    ('xbr', 'xBR \u2014 edge-directed'),
    ('crisp', 'Crisp \u2014 pixelated'),
    ('soft', 'Soft \u2014 plain bilinear (developer control)'),
    ('hd_bilinear', 'HD bilinear \u2014 lightest hq4x'),
]
SCALER_LABELS = dict(SCALER_CHOICES)
SCALER_BY_LABEL = {v: k for k, v in SCALER_CHOICES}

# BUILD 805. The background scaler's black point, in 255ths, written into the
# sdout copy of the shader (ff7nx_shaders.BLACK_POINT_ENV). It can only lower
# a shader's own value, so 12, the HD sets' value, is the top (it leaves the
# files untouched). BUILD 822: the default is 11 (nxsettings.LOCKED and
# ff7nx_shaders.BLACK_POINT_DEFAULT).
BLACK_POINT_CHOICES = [
    (12, 'Black level: 12/255 (max)'),
    (11, 'Black level: 11/255 (default)'),
    (10, 'Black level: 10/255'),
    (9, 'Black level: 9/255'),
    (8, 'Black level: 8/255'),
    (7, 'Black level: 7/255'),
    (6, 'Black level: 6/255'),
    (5, 'Black level: 5/255'),
    (4, 'Black level: 4/255'),
    (3, 'Black level: 3/255'),
    (2, 'Black level: 2/255'),
    (1, 'Black level: 1/255'),
    (0, 'Black level: 0/255 (off)'),
]
BLACK_POINT_LABELS = dict(BLACK_POINT_CHOICES)
BLACK_POINT_BY_LABEL = {v: k for k, v in BLACK_POINT_CHOICES}

# BUILD 815: HD fast is the shipped default, so it is the recommended entry
# and sits second; Off is last.
FXAA_CHOICES = [
    ('', 'Original'),
    ('hd_fast', 'HD fast (recommended)'),
    ('hd', 'HD \u2014 full edge search'),
    ('off', 'Off'),
]
FXAA_LABELS = dict(FXAA_CHOICES)
FXAA_BY_LABEL = {v: k for k, v in FXAA_CHOICES}

VIDEO_SHADER_CHOICES = [
    ('', 'Original'),
    ('hd', 'HD (recommended)'),
]
VIDEO_SHADER_LABELS = dict(VIDEO_SHADER_CHOICES)
VIDEO_SHADER_BY_LABEL = {v: k for k, v in VIDEO_SHADER_CHOICES}

# BUILD 760. Enhanced Stock UI's menu sheets are rebuilt from its 4x DDS at
# this scale (ff7nx_esui.tex_scale). Developer setting; 2 is locked for users.
ESUI_TEXSCALE_CHOICES = [
    (2, '2\u00d7 \u2014 512 px sheets (default, tested on hardware)'),
    (3, '3\u00d7 \u2014 768 px sheets (experimental)'),
    (4, '4\u00d7 \u2014 1024 px, the mod\u2019s full art (experimental)'),
]
ESUI_TEXSCALE_LABELS = dict(ESUI_TEXSCALE_CHOICES)

# BUILD 761/762. Output resolution (ff7nx_dockres). User setting since 762.
DOCK_RES_CHOICES = [
    ('always', '1080p \u2014 always (recommended)'),
    ('1080', 'Dynamic \u2014 1080p docked, 720p handheld'),
    ('', '720p (as shipped)'),
    ('900', '900p when docked (developer)'),
]
DOCK_RES_DEFAULT = 'always'     # BUILD 762: hardware-confirmed, same speed
# BUILD 763b. Title screen background (ff7nx_titleart). User setting.
TITLE_SCREEN_CHOICES = [
    ('sword', 'Buster Sword \u2014 HD, original layout (recommended)'),
    ('mods', 'As the mods set it'),
    ('stock', 'Original (low resolution)'),
]
TITLE_SCREEN_DEFAULT = 'sword'
TITLE_SCREEN_LABELS = dict(TITLE_SCREEN_CHOICES)
TITLE_SCREEN_BY_LABEL = {v: k for k, v in TITLE_SCREEN_CHOICES}
DOCK_RES_LABELS = dict(DOCK_RES_CHOICES)
DOCK_RES_BY_LABEL = {v: k for k, v in DOCK_RES_CHOICES}
ESUI_TEXSCALE_BY_LABEL = {v: k for k, v in ESUI_TEXSCALE_CHOICES}

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


def profile_mod_order(saved):
    """Validated user profile order, highest priority first."""
    raw = (saved or {}).get('__global__', {}).get('mod_load_order', [])
    if not isinstance(raw, list):
        return []
    seen, out = set(), []
    for item in raw:
        if not isinstance(item, str) or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


# BUILD 751. A mod that settings.json does not mention is NEW, and new mods
# start OFF: the distributed settings.json is what turns mods on.
NEW_MOD_ENABLED = False


def migrate_saved_mod_keys(saved, mods):
    """Carry a mod's saved entry across a change of file name that keeps its
    stem -- an .iro replaced by the same mod as an unpacked folder or the
    reverse (Enhanced Stock UI 2.968 ships as a folder; settings.json knows
    it as "Enhanced Stock UI-rel2.iro"). Without this the updated mod would
    look new: OFF, and every one of its options back at the defaults.

    Edits `saved` in place (and returns it). Only an entry whose own file is
    no longer present is carried, so two installed copies never share one.
    """
    if not isinstance(saved, dict):
        return saved
    present = {m.filename for m in mods}

    def stem(name):
        return (name[:-4] if name.lower().endswith('.iro') else name).casefold()

    for mod in mods:
        if mod.filename in saved:
            continue
        for key in list(saved):
            if (key == '__global__' or key in present
                    or not isinstance(saved.get(key), dict)):
                continue
            if stem(key) == stem(mod.filename):
                saved[mod.filename] = saved[key]
                break
    return saved


def saved_enabled(saved, mod):
    """Is `mod` enabled in settings.json? A mod it does not list is new."""
    return bool((saved or {}).get(mod.filename, {}).get('enabled',
                                                         NEW_MOD_ENABLED))


def _mod_options_from_saved(mod, saved):
    """Merge saved values with the current manifest's defaults.

    IRO updates can add options or occasionally rename one.  Replacing the
    old ``options`` dict wholesale made a newly-added option disappear from
    build-time gating, and Ninostyle Chibi 26.08302 specifically renamed
    ``efryt fb`` to ``efryt``.  Preserve explicit user values, migrate that
    known stable ID, and let every new option use its author-declared default.
    """
    manifest = getattr(mod, 'manifest', None)
    defaults = manifest.defaults() if manifest else {}
    stored = (saved or {}).get(mod.filename, {}).get('options', {})
    if not isinstance(stored, dict):
        stored = {}
    merged = dict(defaults)
    merged.update(stored)
    if (_manifest_id(mod) == '2b25060c-c60e-426e-ac46-f85439e903e2'
            and 'efryt' not in stored and 'efryt fb' in stored):
        merged['efryt'] = stored['efryt fb']
    return merged


def _manifest_id(mod):
    return ((getattr(getattr(mod, 'manifest', None), 'mod_id', '') or '')
            .strip().lower())


def _name_key(name):
    """7th Heaven's name order (BUILD 619).

    7H sorts names with .NET's default, culture-aware comparer, not by code
    point: whitespace, then punctuation, then symbols, then digits, then
    letters, case-insensitively. `casefold()` order agreed with that for
    every mod we had, and disagrees the first time a name starting with "["
    meets one starting with a digit -- "[Tsunamods] Idle Animations - 60fps"
    against "60/30 FPS Gameplay", both category Animations. 7H puts the idle
    mod ABOVE 60 FPS (its 60 fps idle clips win the shared names, as its
    author intends); code-point order put it below.
    """
    import unicodedata
    out = []
    for ch in name:
        if ch.isspace():
            out.append((0, 0, ''))
            continue
        cat = unicodedata.category(ch)
        if cat[0] == 'P':
            out.append((1, ord(ch), ''))
        elif cat[0] == 'S':
            out.append((2, ord(ch), ''))
        elif cat == 'Nd':
            out.append((3, int(unicodedata.digit(ch, 0)), ''))
        else:
            out.append((4, 0, ch.casefold()))
    return tuple(out)


def _autosort_7h(mods):
    """Exactly mirror 7th Heaven's AutoSortBasedOnCategory().

    7H does *not* topologically sort Before/After declarations.  It first
    sorts by category and name, then walks that original sorted snapshot and
    moves each declaring mod immediately before/after its named target.  The
    distinction is observable with this set: a graph sort moves Cosmos FMV
    after a transitive chain, while 7H correctly leaves it below Echo-S and
    above 60 FPS.  Keeping this deliberately procedural also matches 7H's
    behavior for imperfect third-party constraints.
    """
    ordered = sorted(mods, key=lambda m: (mod_load_rank(m),
                                           _name_key(m.display_name)))
    by_id = {_manifest_id(mod): mod for mod in ordered if _manifest_id(mod)}
    # Snapshot iteration is intentional: it is the `sortedList.ToList()` in
    # 7H's MyModsViewModel.AutoSortBasedOnCategory().
    for mod in list(ordered):
        manifest = getattr(mod, 'manifest', None)
        if not manifest:
            continue
        for target_id in getattr(manifest, 'order_after', ()):
            target = by_id.get(target_id)
            if target is not None and ordered.index(target) > ordered.index(mod):
                ordered.remove(mod)
                ordered.insert(ordered.index(target), mod)
        for target_id in getattr(manifest, 'order_before', ()):
            target = by_id.get(target_id)
            if target is not None and ordered.index(target) < ordered.index(mod):
                ordered.remove(mod)
                ordered.insert(ordered.index(target), mod)
    return ordered


def _active_order_constraints(mods):
    """Declared order edges present in this enabled profile, for the log."""
    by_id = {_manifest_id(m): m for m in mods if _manifest_id(m)}
    out, seen = [], set()
    for mod in mods:
        manifest = getattr(mod, 'manifest', None)
        for target_id in getattr(manifest, 'order_before', ()):
            target = by_id.get(target_id)
            if target and (mod.filename, target.filename) not in seen:
                seen.add((mod.filename, target.filename))
                out.append((mod, target))
        for target_id in getattr(manifest, 'order_after', ()):
            target = by_id.get(target_id)
            if target and (target.filename, mod.filename) not in seen:
                seen.add((target.filename, mod.filename))
                out.append((target, mod))
    return out


def discover_mods(order=None, moved=None):
    """Mods in priority order, highest first: data/mod_sources.json's tested
    order for the mods it lists, 7th Heaven's category auto-sort below them
    for the rest (BUILD 831) -- then every mod the user dragged (`moved`,
    BUILD 849) put back where `order` (settings.json's mod_load_order) has
    it. See apply_manual_moves."""
    if not os.path.isdir(MODS_DIR):
        return []
    found = []
    for fn in sorted(os.listdir(MODS_DIR)):
        full = os.path.join(MODS_DIR, fn)
        # BUILD 619: an unpacked mod folder (mod.xml at its root) is a mod
        # too, exactly as in 7th Heaven PC -- same options, same priority.
        is_folder = (os.path.isdir(full) and not fn.startswith('.')
                     and iro.folder_path(full, 'mod.xml') is not None)
        if fn.lower().endswith('.iro') or is_folder:
            mod = build.Mod(full, CACHE_DIR)
            # Cheap: reads mod.xml only, no extraction. Needed up front so
            # the category is known before we sort.
            mod._load_manifest()
            found.append(mod)
    return apply_manual_moves(apply_reference_order(_autosort_7h(found)),
                              order, moved)


# BUILD 849. MANUAL PRIORITY. A mod data/mod_sources.json does not list (a
# mod this program does not know, or one whose mod.xml is misconfigured)
# lands below every supported mod -- the only place the program can put it
# without knowing what it overrides. The user can drag such a mod (press and
# hold its row) to the priority it needs. settings.json keeps the list order
# (`mod_load_order`, written on every save) and which mods were dragged
# (`mod_moved`); everything else keeps the computed order, so a supported
# mod's tested place never changes. "Restore Priority" forgets the drag.
MOVED_KEY = 'mod_moved'


def manual_moves(saved):
    """(order, moved) from settings.json: the saved list order and the set
    of mods the user dragged."""
    g = (saved or {}).get('__global__', {})
    raw = g.get(MOVED_KEY, [])
    moved = {x for x in raw if isinstance(x, str)} \
        if isinstance(raw, list) else set()
    return profile_mod_order(saved), moved


def apply_manual_moves(mods, order, moved):
    """`mods` (the computed order) with each dragged mod back where `order`
    has it: right below the nearest mod above it in `order` that is already
    placed (the top if none). Undragged mods keep their computed order, so
    a mod installed later still lands where it belongs, and a dragged mod
    keeps its neighbour. A dragged mod `order` does not list stays put."""
    if not moved or not order:
        return list(mods)
    by_name = {m.filename: m for m in mods}
    pos = {name: i for i, name in enumerate(order)}
    drag = [n for n in order if n in moved and n in by_name]
    if not drag:
        return list(mods)
    dragged = set(drag)
    out = [m for m in mods if m.filename not in dragged]
    for name in drag:
        anchor = None
        for prev in reversed(order[:pos[name]]):
            if prev in by_name and any(m.filename == prev for m in out):
                anchor = prev
                break
        at = 0 if anchor is None else \
            next(i for i, m in enumerate(out) if m.filename == anchor) + 1
        out.insert(at, by_name[name])
    return out


def reference_listed(mods, sources=None):
    """File names of the mods data/mod_sources.json lists (matched as
    apply_reference_order matches them): the supported mods, whose place
    is the tested one and cannot be dragged."""
    if sources is None:
        sources = _reference_sources()
    if not sources:
        return set()

    def stem(name):
        return (name[:-4] if name.lower().endswith('.iro')
                else name).casefold()

    files = {(s.get('file') or '').casefold() for s in sources}
    stems = {stem(s.get('file') or '') for s in sources}
    ids = {str(s['id']).strip().lower() for s in sources if s.get('id')}
    return {m.filename for m in mods
            if m.filename.casefold() in files or stem(m.filename) in stems
            or (_manifest_id(m) and _manifest_id(m) in ids)}


def _reference_sources():
    """data/mod_sources.json's mods, in its order: the tested load order."""
    try:
        import ff7nx_download
        return ff7nx_download.load_sources()
    except Exception:                                          # noqa: BLE001
        return []


def apply_reference_order(mods, sources=None):
    """BUILD 831. The list, and so the build's priority, in the TESTED order.

    data/mod_sources.json lists the supported mods in the order they were
    played through on hardware, highest priority first; it is also the
    Install Mods window's order and the README's table. Every mod it lists
    takes that place, however and whenever it was installed.

    This replaces keeping the saved order (BUILD 753's profile order): that
    copied 7th Heaven, where a new mod goes to the BOTTOM until you press
    Auto-sort or drag it. This program has neither, so a mod installed later
    simply stayed last -- Echo-S downloaded last ended up with the lowest
    priority, in the list and in the build -- and nothing could move it.

    A mod is matched to its source by the file name Install Mods gives it,
    then by stem (an .iro replaced by the same mod as a folder, or the
    reverse), then by its mod.xml ID against the catalog ID (the same mod
    saved under another name). Mods the file does not list keep 7th Heaven's
    category auto-sort among themselves, below the supported ones."""
    if sources is None:
        sources = _reference_sources()
    if not sources:
        return list(mods)

    def stem(name):
        return (name[:-4] if name.lower().endswith('.iro')
                else name).casefold()

    by_file, by_stem, by_id = {}, {}, {}
    for i, src in enumerate(sources):
        f = src.get('file') or ''
        by_file.setdefault(f.casefold(), i)
        by_stem.setdefault(stem(f), i)
        if src.get('id'):
            by_id.setdefault(str(src['id']).strip().lower(), i)
    known, other = [], []
    taken = set()
    for m in mods:
        r = by_file.get(m.filename.casefold())
        if r is None:
            r = by_stem.get(stem(m.filename))
        if r is None:
            r = by_id.get(_manifest_id(m))
        if r is not None and r not in taken:
            taken.add(r)
            known.append((r, m))
        else:
            other.append(m)
    known.sort(key=lambda t: t[0])
    return [m for _r, m in known] + other


def apply_profile_order(mods, order):
    """BUILD 753. 7th Heaven's list is the PROFILE's order: it is kept as it
    is when a mod is updated (even when the update changes the mod's category
    or its Before/After lines), and the category auto-sort runs only when
    asked for. A mod the profile does not list yet is added at the bottom
    (`ProfileItem` appended, SortOrder 9999). settings.json's
    `mod_load_order` is that profile: mods it lists keep its order, new ones
    follow in auto-sort order. With no saved order the auto-sort is the
    list, as a fresh 7th Heaven install shows it.

    A name is matched exactly, then by stem, so an .iro replaced by the same
    mod as a folder (or the reverse) keeps its place."""
    if not order:
        return list(mods)

    def stem(name):
        return (name[:-4] if name.lower().endswith('.iro') else name).casefold()

    rank = {}
    for i, name in enumerate(order):
        rank.setdefault(name, i)
    present = {m.filename for m in mods}
    listed, new = [], []
    for m in mods:
        r = rank.get(m.filename)
        if r is None:
            s = stem(m.filename)
            r = next((i for n, i in rank.items()
                      if n not in present and stem(n) == s), None)
        (listed if r is not None else new).append((r, m))
    listed.sort(key=lambda t: t[0])
    return [m for _r, m in listed] + [m for _r, m in new]


def _option_key(settings, ident):
    """Actual settings key for an IRO option ID, ignoring author casing."""
    want = str(ident).casefold()
    return next((key for key in settings if str(key).casefold() == want), None)


def _option_value(mod, settings, ident):
    key = _option_key(settings, ident)
    if key is not None:
        return settings[key]
    manifest = getattr(mod, 'manifest', None)
    option = next((o for o in getattr(manifest, 'options', ())
                   if o.id.casefold() == str(ident).casefold()), None)
    return option.default if option is not None else None


# The 60/30 FPS Gameplay mod. Its FPS Mode option decides which of its
# animation folders install, so it also decides whether our frame-rate
# patches match them: 1 Original FPS, 2 Full 30 FPS, 3 Full 60 FPS.
FPS_MOD_ID = 'c0d1e191-a251-465d-8a3c-a3be5366d849'
FPS_MODE_OPTION = 'FPSMode'


def _fps_patch_mode(fps_on, active, settings, log):
    """The 60/30 FPS Patches setting, resolved against the mod's FPS Mode.

    Returns the rate the patch set targets: 60, 30, or 0 for off.
    Full 60 FPS (and no 60/30 mod at all) keeps the setting as it is: 60 when
    it is on. Full 30 FPS builds the 30 FPS set -- battles at 30, field and
    world at their stock 30 -- to match the mod's 30 FPS files. Original FPS
    turns the set off, since the mod then ships stock-rate animations.
    """
    if not fps_on:
        return 0
    mod = next((m for m in active if _manifest_id(m) == FPS_MOD_ID), None)
    if mod is None:
        return 60
    try:
        mode = int(_option_value(mod, settings.get(mod.filename, {}),
                                 FPS_MODE_OPTION))
    except (TypeError, ValueError):
        return 60
    if mode == 1:
        log('60/30 FPS Patches: off, %s is set to Original FPS'
            % mod.display_name)
        return 0
    if mode == 2:
        log('60/30 FPS Patches: Full 30 FPS, following %s'
            % mod.display_name)
        return 30
    return 60


def _compatibility_profile(mods, enabled, settings_by_mod, log):
    """Return active mods and safe effective settings, or report a hard stop.

    IRO compatibility rules are declarations, not heuristics. A forbidden or
    missing required mod stops the build instead of silently dropping either
    selected mod. A conditional Setting rule, however, names the exact option
    and exact value that makes the pair compatible, so it is safely applied
    to this build's in-memory settings and recorded in the build log.
    """
    active = [m for m in mods if enabled.get(m.filename)]
    # BUILD 849: PC-only add-ons (ff7nx_esui.PC_ONLY) are left out whole,
    # before any compatibility rule can touch the other mods' settings.
    for m in [m for m in active if ff7nx_esui.pc_only(m)]:
        active.remove(m)
        log('%s: left out -- %s' % (m.display_name, ff7nx_esui.pc_only(m)[1]))
    by_id = {_manifest_id(m): m for m in active if _manifest_id(m)}
    effective = {m.filename: dict(settings_by_mod.get(m.filename, {}))
                 for m in active}
    bad = []
    for mod in active:
        manifest = getattr(mod, 'manifest', None)
        for required in getattr(manifest, 'compat_requires', ()):
            if required not in by_id:
                bad.append('%s requires mod ID %s, which is not enabled'
                           % (mod.display_name, required))
        for forbidden in getattr(manifest, 'compat_forbids', ()):
            target = by_id.get(forbidden)
            if target is not None:
                pair = tuple(sorted((mod.filename, target.filename)))
                if pair not in {p[0] for p in bad if isinstance(p, tuple)}:
                    bad.append((pair, '%s forbids %s'
                                % (mod.display_name, target.display_name)))
    if bad:
        log('ERROR: declared IRO compatibility conflict(s); no output was built:')
        for item in bad:
            log('  - ' + (item[1] if isinstance(item, tuple) else item))
        log('Resolve the listed selection in the mod UI, then build again.')
        return None, None

    # One rule can enable another; iterate to a fixed point. A bounded loop
    # prevents malformed third-party manifests from spinning forever.
    changed = []
    for _pass in range(sum(len(getattr(m.manifest, 'compat_settings', ()))
                           for m in active) + 1):
        made_change = False
        for mod in active:
            manifest = getattr(mod, 'manifest', None)
            mine = effective[mod.filename]
            for my_id, my_value, target_id, their_id, required in \
                    getattr(manifest, 'compat_settings', ()):
                target = by_id.get(target_id)
                if target is None:
                    continue  # The rule applies only when both mods are on.
                try:
                    active_now = int(_option_value(mod, mine, my_id))
                    trigger = int(my_value)
                    want = int(required)
                except (TypeError, ValueError):
                    continue
                if active_now != trigger:
                    continue
                # BUILD 641b: Nitpick is an add-on; its rules that switch
                # another mod's feature off (Cosmos Limit Break's field art)
                # are not applied. See ff7nx_nitpick.ignores_rule.
                if build.ff7nx_nitpick.ignores_rule(mod, want):
                    continue
                target_settings = effective[target.filename]
                target_key = _option_key(target_settings, their_id)
                if target_key is None:
                    option = next((o for o in getattr(target.manifest, 'options', ())
                                   if o.id.casefold() == their_id.casefold()), None)
                    if option is None:
                        log('  ! compatibility rule ignored: %s names unknown '
                            'option %s on %s' % (mod.display_name, their_id,
                                                  target.display_name))
                        continue
                    target_key = option.id
                old = _option_value(target, target_settings, target_key)
                try:
                    old_value = int(old)
                except (TypeError, ValueError):
                    # A third-party profile can contain an old textual value
                    # for a Bool option.  The manifest's exact declared
                    # value remains the safe correction; don't let malformed
                    # saved state abort an otherwise valid build.
                    old_value = None
                if old_value != want:
                    target_settings[target_key] = want
                    changed.append((mod.display_name, my_id, trigger,
                                    target.display_name, target_key, old, want))
                    made_change = True
        if not made_change:
            break
    if changed:
        log('IRO compatibility: applied %d declared setting correction(s) '
            'for this build:' % len(changed))
        for source, mine, value, target, theirs, old, new in changed:
            log('  %s (%s=%d) -> %s: %s %r -> %d'
                % (source, mine, value, target, theirs, old, new))
    return active, effective


# THE SFX TABLES LIVE IN `main` AND THEIR SAMPLES LIVE IN `audio.dat`.
#
# `ff7nx_sfxshuffle`'s route table and `sfxmod`'s appended payload region are
# one thing in two files: the table says "route 12's third variant is at this
# offset past the fixed archive", and the offset is only true of the exact
# `audio.dat` the same build wrote. Copy one without the other and every
# variant read lands somewhere else in a 79 MB file, which sounds like the
# samples have been corrupted -- attacks play correctly the first time and
# turn to crackle as the sequential selector advances into the appended
# region.
#
# Build 346 did exactly that: a Cosmo Memory sfx folder lost its .ogg files,
# the bridge fell from 58 routes and 13.1 MB to 21 and 3.3 MB, and a
# `main`-only copy left the console reading the old layout through the new
# table. Nothing in the build said a word, because each half was internally
# consistent. This is that word.
AUDIO_PAIR = (os.path.join('data', 'sound', 'audio.dat'),
              os.path.join('data', 'sound', 'audio.fmt'))


def _audio_fingerprint():
    """(size, sha256) per file of the SFX archive `main` is indexed against."""
    import hashlib
    out = {}
    for name in AUDIO_PAIR:
        path = os.path.join(SDOUT_DIR, 'atmosphere', 'contents',
                            build.TITLE_ID, build.ROMFS, name)
        try:
            with open(path, 'rb') as handle:
                digest = hashlib.sha256()
                for block in iter(lambda: handle.read(1 << 20), b''):
                    digest.update(block)
                out[name] = (os.path.getsize(path), digest.hexdigest())
        except OSError:
            out[name] = None
    return out


import time  # noqa: E402  (BUILD 835: _BuildClock)


class _Sampler:
    """BUILD 837, SEVENTH_NX_PROFILE=1: a sampling profiler for one build.

    A background thread looks at the build thread's stack four times a
    second and counts every function on it, so the report says where the
    build's WALL time went -- including time spent waiting on ffmpeg or a
    thread pool, which a CPU profiler would miss -- for a cost that does not
    show next to a build. Only this program's own functions are listed."""

    PERIOD = 0.25

    def __init__(self, ident):
        self.ident, self.n = ident, 0
        self.incl, self.self_ = {}, {}
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    @staticmethod
    def _ours(code):
        f = code.co_filename.replace('\\', '/')
        return ('/src/' in f or f.endswith('7th_heaven_nx.py')
                or '/' not in f)

    def _run(self):
        while not self._stop.wait(self.PERIOD):
            frame = sys._current_frames().get(self.ident)
            seen = set()
            inner = None
            while frame is not None:
                code = frame.f_code
                if self._ours(code):
                    key = '%s:%s' % (os.path.basename(code.co_filename)
                                     .rsplit('.', 1)[0], code.co_name)
                    if inner is None:
                        # the innermost of ours, at the line it is on
                        inner = '%s:%d' % (key, frame.f_lineno)
                    if key not in seen:
                        seen.add(key)
                        self.incl[key] = self.incl.get(key, 0) + 1
                frame = frame.f_back
            if inner is not None:
                self.self_[inner] = self.self_.get(inner, 0) + 1
            self.n += 1

    def report(self, top=25):
        self._stop.set()
        self._t.join(1.0)
        if not self.n:
            return []
        out = ['profile (%d samples, share of build time spent inside each '
               'function, callees included):' % self.n]
        for key, c in sorted(self.incl.items(), key=lambda kv: -kv[1])[:top]:
            out.append('  %5.1f%%  %8.0fs  %s' % (100.0 * c / self.n,
                                                  c * self.PERIOD, key))
        out.append('hot lines (where the time is actually spent):')
        for key, c in sorted(self.self_.items(),
                             key=lambda kv: -kv[1])[:top]:
            out.append('  %5.1f%%  %8.0fs  %s' % (100.0 * c / self.n,
                                                  c * self.PERIOD, key))
        return out


class _BuildClock:
    """BUILD 835. Where a build's time went, at no cost to the build.

    Every log line is stamped as it is written, and the time until the next
    line is charged to the step that line announced ("building flevel.lgp
    ...", "encoding Echo-S voice ..."). At the end the slowest steps are
    listed, with the machine facts that decide speed, so a report of a slow
    build says what was slow instead of leaving it to guesswork."""

    TOP = 12

    def __init__(self, log):
        self._log = log
        self.t0 = self._last_t = time.monotonic()
        self._last = None
        self.steps = []
        self._closing = []      # BUILD 850: said after the time summary
        self._prof = None
        if os.environ.get('SEVENTH_NX_PROFILE', '').strip() == '1':
            self._prof = _Sampler(threading.get_ident())

    def __call__(self, text):
        now = time.monotonic()
        if self._last is not None:
            self.steps.append((now - self._last_t, self._last))
        line = str(text).strip()
        if line:
            self._last, self._last_t = line, now
        self._log(text)

    def defer(self, fn):
        """Run `fn` (it logs the build's closing words: what to copy to
        the SD card) after the time summary, so those are the last lines."""
        self._closing.append(fn)

    def header(self):
        try:
            import lgp
            lzs = ('native C library' if lgp._native_lib() is not None
                   else 'Python routines (slower)')
        except Exception:                                      # noqa: BLE001
            lzs = 'unknown'
        import platform
        self._log('machine: %s %s, %d CPU threads, LZS: %s'
                  % (platform.system(), platform.machine(),
                     nxpaths.cpu_threads(), lzs))

    def summary(self):
        now = time.monotonic()
        if self._last is not None:
            self.steps.append((now - self._last_t, self._last))
            self._last = None
        total = now - self.t0

        def hms(sec):
            sec = int(round(sec))
            return ('%dh %02dm %02ds' % (sec // 3600, sec // 60 % 60, sec % 60)
                    if sec >= 3600 else '%dm %02ds' % (sec // 60, sec % 60)
                    if sec >= 60 else '%ds' % sec)
        slow = sorted(self.steps, reverse=True)[:self.TOP]
        slow = [(t, line) for t, line in slow if t >= 5]
        self._log('')
        self._log('build time: %s' % hms(total))
        if self._prof is not None:
            for line in self._prof.report():
                self._log(line)
        if slow:
            self._log('slowest steps (time until the next log line):')
            for t, line in slow:
                self._log('  %9s  %s' % (hms(t), line[:110]))
        closing, self._closing = self._closing, []
        for fn in closing:
            try:
                fn()
            except Exception as exc:                           # noqa: BLE001
                self._log('  ! %s' % exc)


def run_build(mods, enabled, settings_by_mod, log, progress,
              fps_60=False):
    """The build, timed (BUILD 835): see _BuildClock."""
    clock = _BuildClock(log)
    clock.header()
    try:
        return _run_build_steps(mods, enabled, settings_by_mod, clock,
                                progress, fps_60=fps_60)
    finally:
        try:
            clock.summary()
        except Exception:                                      # noqa: BLE001
            pass


def _run_build_steps(mods, enabled, settings_by_mod, log, progress,
                     fps_60=False):
    # BUILD 326. Publish the 60 FPS setting so the ARCHIVE CACHE can see it.
    # It arrives here as a GUI checkbox / --60fps flag, which means
    # `_archive_fingerprint` was blind to it -- and it is the one setting
    # outside that function which changes an archive's bytes:
    # `apply_fps_patches` rewrites the finished battle.lgp with every
    # animation wait scaled x4. build.py folds this into battle.lgp's key
    # only, so toggling it cannot invalidate the other four archives.
    os.environ[build.FPS_ENV] = '1' if fps_60 else '0'
    os.environ[build.FPS_MODE_ENV] = '60'

    # BUILD 754: record which cache entries this build reads (ff7nx_cachegc).
    try:
        import ff7nx_cachegc
        ff7nx_cachegc.start()
    except Exception:                                           # noqa: BLE001
        pass

    audio_before = _audio_fingerprint()

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

    active, effective_settings = _compatibility_profile(
        mods, enabled, settings_by_mod, log)
    if active is None:
        return False
    if not active:
        log('nothing enabled.')
        return False
    fps_rate = _fps_patch_mode(fps_60, active, effective_settings, log)
    fps_60 = bool(fps_rate)
    os.environ[build.FPS_ENV] = '1' if fps_60 else '0'
    os.environ[build.FPS_MODE_ENV] = '30' if fps_rate == 30 else '60'

    log('')
    for mod in active:
        name = mod.display_name
        progress(0, 0, f'Checking {name}')
        mod.ensure_extracted(
            log, lambda i, n, name=name: progress(i, n, f'Extracting {name}'))

    log('')
    constraints = _active_order_constraints(active)
    if constraints:
        log('IRO manifest order constraints applied:')
        for before, after in constraints:
            log('  %s  ->  %s' % (before.display_name, after.display_name))
        log('')
    log('mod priority (7th Heaven profile order, first listed wins):')
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

            def _with_hext(paths, _eb=exe_bytes):
                # BUILD 763: the same reader over the exe with this build's
                # HEXT applied (what 7th Heaven's live memory would hold).
                out = _eb
                for p in paths:
                    with open(p, encoding='utf-8', errors='replace') as f:
                        out, _a, _s = exe_patch.apply_hext(out, f.read())
                pe2 = exe_patch.parse_pe(out)
                return iro.exe_var_reader(
                    out, lambda va: exe_patch.va_to_offset(pe2, va))
            runtime_read.with_hext = _with_hext
        except Exception as exc:                               # noqa: BLE001
            log(f'note: RuntimeVar gates not evaluated ({exc}); every '
                'conditional folder is kept')
    progress(0, 0, 'Planning which mod files go where')
    plan = build.build_plan(list(reversed(active)),
                            effective_settings, catalogs, log,
                            music_names=music_names,
                            runtime_read=runtime_read)

    log('')
    log(f'portable files : {plan.total_portable()}')
    if plan.skipped_ffnx:
        log(f'FFNx textures  : {plan.skipped_ffnx} unsupported external '
            'image(s) skipped')
    if plan.world_dds_native_names:
        log(f'Gaia world TEX : {len(plan.world_dds_native_names)} DDS '
            'source(s) queued for native world_us.lgp conversion')
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
    # HEXT may just have produced, so they cannot run any earlier. With the
    # set off this still runs for the input and FMV tweaks, and does nothing
    # when none of them is on.
    produced += build.apply_fps_patches(SDOUT_DIR, DUMP, log, produced)
    # And the pillarbox fix after THAT, for the same reason: it edits
    # exefs/main, so it has to build on whatever the 60 FPS set just wrote
    # rather than on the dump's copy. Gated on its own env var, so a build
    # with it off is byte-identical to before this existed.
    produced += build.apply_widescreen(SDOUT_DIR, DUMP, log, produced)
    # 16:9 field, last, for the third time for the same reason -- it edits
    # exefs/main too, and it edits flevel.lgp, so it has to see whatever the
    # passes above just wrote.
    #
    # These three are mutually exclusive by construction. The first two are
    # historical diagnostic-only modes; the last is the GUI's recommended
    # `ws-3d` mode (or plain `ws` plus the explicit framing override):
    #   apply_widescreen     fires only for 'stretch' / 'fit'
    #   ff7nx_field169       fires only for 'field'
    #   ff7nx_ws.apply_module fires for GUI `ws-3d`, or `ws` AND the override
    # All three remain reachable by environment variable for a diagnostic
    # run. The GUI's `ws-3d` mode bakes the data half inside _build_flevel and
    # reaches the framing transaction here afterward.
    produced += ff7nx_field169.apply(SDOUT_DIR, DUMP, log, produced)
    produced += build.ff7nx_ws.apply_module(SDOUT_DIR, DUMP, log, produced)
    # Gaia's archive conversion enlarges native indexed world TEX files by
    # one uniform factor. The stock mesh still carries native pixel UVs, so
    # its six normalisations must receive that same factor or every polygon
    # stretches only the upper-left crop of its texture. Gated automatically
    # by the active Gaia DDS plan; vanilla/non-Gaia builds do nothing here.
    produced += build.apply_gaia_world_uv(SDOUT_DIR, DUMP, log, produced)
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
    # The GRAPHICS pool, immediately after it and on its output. A different
    # pool entirely -- nv::InitializeGraphics at +0x113BD7C, where the GL
    # driver keeps textures and render targets -- and the one every texture
    # ceiling this project raised actually lands on. Two in-place immediates,
    # no cave, no archive built to match it, so it is safe to sit last.
    # FINDINGS-302; the setting is the Graphics memory dropdown.
    produced += build.apply_gfxpool(SDOUT_DIR, DUMP, log, produced)
    # THE END-OF-FRAME CRASH FIX. Last module pass, on everything above it.
    # The port's OpenGL error reporter -- debug code left in a retail build --
    # is what nine identical crash reports were actually dying in; the frame
    # chain pointed at gfx_drv_flip because the reporter is reached by a TAIL
    # call and inherits its return address. Thirteen in-place immediates, no
    # cave, no archive. Not a GUI setting: it is a defect fix, like
    # ff7nx_heap's NO_HEAP_DUMP. FINDINGS-303.
    produced += build.apply_glerror(SDOUT_DIR, DUMP, log, produced)
    # The texture-cache leak. free() parks surfaces in a (w,h)-keyed multimap
    # that nothing but a same-size create ever empties -- 16.5 MB of dead
    # weight in vanilla, 166.5 MB in this build, out of a 256 MB pool. One
    # word, and DEFAULT OFF: the mechanism is measured but the graphics pool
    # was believed on good grounds too. FINDINGS-306.
    produced += build.apply_texcache(SDOUT_DIR, DUMP, log, produced)
    # BUILD 603. The background scaler's filtered copy of every texture is
    # 4x width AND height -- 17x its memory. fship's 512x512 textures ran the
    # graphics pool out (GL_OUT_OF_MEMORY, the crew's speckles). Capped at
    # 1024 px a side; stock-size art unchanged. Two words, two padding caves.
    produced += build.apply_texscale(SDOUT_DIR, DUMP, log, produced)
    # BUILD 604. World-map shadows wrote rhw = 1.0 (2D to the shader), so
    # they kept the game's old depth and showed through the crater lip. They
    # now carry the nearest corner's 1/w: the terrain's depth formula.
    # 604b: and a shadow's size stops at 0 high up instead of inverting.
    produced += build.apply_shadowdepth(SDOUT_DIR, DUMP, log, produced)
    # BUILD 620. Double Input Bugfix: the mod's x86 code patches as ARM64
    # words (four at most, in place). Nothing unless the mod is enabled.
    produced += build.apply_difix(SDOUT_DIR, DUMP, log, produced)
    # Resized SYW TEX payloads carry a per-file integer scale in two inert
    # header words. All three u/v reciprocal pairs in _load_texture apply that
    # scale when each graphics object is constructed, so every consumer keeps
    # native logical texels while its GPU image is larger. Texture dimensions,
    # .s tables and on-screen quad extents remain stock.
    produced += build.apply_spelluv(
        SDOUT_DIR, DUMP, log, produced,
        needed=bool((plan.spell_dds
                     and build.ff7nx_spelltex.uniform_scale() > 1)
                    or (plan.minigame_dds
                        and build.ff7nx_minigametex.cap() > 0)
                    # BUILD 621: ESUI menu sheets above their native size
                    or (build.ff7nx_esui.CURRENT is not None
                        and build.ff7nx_esui.tex_scale() > 1)
                    # BUILD 641: Nitpick's title / portrait / credits art
                    or (build.ff7nx_nitpick.CURRENT is not None
                        and build.ff7nx_nitpick.CURRENT.get('textures')
                        and build.ff7nx_nitpick.tex_scale() > 1)
                    # BUILD 763: a mod's title background (4x)
                    or (build.ff7nx_titleart.CURRENT is not None
                        and build.ff7nx_titleart.CURRENT.get('built'))))
    # The framebuffer-texture capture rect. The floor-warp summons snapshot
    # the frame and map it onto their mesh; the capture converts the rect to
    # staging-surface columns with a hardcoded identity that assumes a 640
    # unit wide frame, and 16:9 makes it 854 spanning -107..747. Applied only
    # where xscale == 1 -- the page-scaled captures (the battle-entry swirl)
    # are left byte-identical. One word plus a branch-free cave in padding.
    # ON with 16:9 and OFF at 4:3 where the identity is already right -- the
    # same gate as ff7nx_battlewide. FINDINGS-247.
    produced += build.apply_fbcapture(SDOUT_DIR, DUMP, log, produced)
    # Dynamic weapons: which mesh a model part resolves to is decided when
    # the game opens the file, from the character's equipped weapon in the
    # savemap. Last of the module patches, so its cave takes whatever padding
    # the others left. Does nothing at all unless `ff7nx_dynweapon` put the
    # per-weapon variants in the archives. See FINDINGS-412.
    produced += build.apply_dynweapon(SDOUT_DIR, DUMP, log, produced)
    # ------------------------------------------------------------------
    # Cosmo Memory's native audio bridges.
    #
    # These go here, after every visual and input patch, for the ordinary
    # reason: whoever edits `exefs/main` last has to see what everyone else
    # wrote. Their caves come out of ff7nx_cave's padding pool, which
    # re-checks that each hole is still zero in the module being patched, so
    # taking them last costs the earlier features nothing.
    #
    # THE ORDER AMONG THEM IS NOT ARBITRARY. `apply_field_footsteps`
    # shares the world bridge's BSS block and its physical archive row, and
    # takes the address from the report `apply_world_footsteps` records on
    # the plan -- so world must come first, and the sequential bridge (which
    # grows BSS by its own 232 bytes) must not be able to come between them
    # in a way that moves what the field bridge reads. Passing the address
    # explicitly is what makes that safe rather than merely conventional.
    #
    # `apply_ambient` goes last of the five. It is the only one that needs
    # contiguous module data (a 918-byte field map on the shipping option
    # set), so taking it last means it sees exactly what the others left and
    # reports a real shortfall instead of pre-empting them. Its field hook is
    # 0x947CF4, one instruction AFTER analog-360's -- the two chain, they do
    # not contend; see ff7nx_ambient.
    #
    # Each one fails closed on its own: a bridge that cannot be installed is
    # reported and skipped, and everything else -- including the ordinary
    # 718-row archive replacement, which needs no module patch at all --
    # still works.
    produced += build.apply_sfx_shuffle(SDOUT_DIR, DUMP, plan, log, produced)
    produced += build.apply_battle_sfx(SDOUT_DIR, DUMP, plan, log, produced)
    # BUILD 738. Cosmo Memory's Center Battle SFX, from its <FFNxConfig>: one
    # word in the DirectSound shim's SetPan, no cave. See ff7nx_sfxcenter.
    produced += build.apply_sfx_center(SDOUT_DIR, DUMP, plan, log, produced)
    produced += build.apply_world_footsteps(SDOUT_DIR, DUMP, plan, log,
                                            produced)
    produced += build.apply_field_footsteps(SDOUT_DIR, DUMP, plan, log,
                                            produced)
    produced += build.apply_ambient(SDOUT_DIR, DUMP, plan, log, produced)
    # ------------------------------------------------------------------
    # Echo-S. Its field scripts are FFNx-authored, so the two unconditional
    # music-compatibility patches FFNx applies to the PC exe have to exist
    # here too or the merged scripts come up with no field BGM and the wrong
    # battle music. Nothing to do with voice; it runs whenever the scripts
    # were taken, and unlike the Cosmo bridges it stops the build rather than
    # shipping an flevel whose other half is missing. Neither of its two
    # sites is touched by anything else this project installs -- both were
    # read out of the real shipping module; see ff7nx_echomusic.
    produced += build.apply_echo_music(SDOUT_DIR, DUMP, plan, log, produced)
    # The dialogue runtime, LAST of the module passes and after apply_ambient
    # specifically. Three of its sixteen sites are already owned -- analog-360
    # at 0x947CF0 and the ambient battle/world services at 0x8FB20 and
    # 0xF1E0EC -- and it chains in front of them rather than displacing them.
    # It can only do that because it runs second: `ff7nx_ambient` verifies the
    # STOCK word at its sites and refuses to install over a branch, so the
    # reverse order stops the build with the site named instead of composing
    # something subtly wrong. See ff7nx_voice.
    produced += build.apply_echo_voice(SDOUT_DIR, DUMP, plan, log, produced)
    # The facial runtime, after the voice one and for two reasons. Its lip
    # flap reads `ff7nx_voice`'s live-player pointer, so it needs that pass's
    # BSS address, which `apply_echo_voice` records on the plan. And it is the
    # largest remaining consumer of the padding pool -- about 1,450 of the
    # 1,763 usable words left after everything else -- so it goes last of the
    # two and reports what it took rather than pre-empting anyone. It does
    # nothing at all unless SEVENTH_NX_FACIAL shipped the art. See
    # ff7nx_facial.
    produced += build.apply_facial(SDOUT_DIR, DUMP, plan, log, produced)
    # Echo-S's Day/Night cycle, after the facial runtime and for the same
    # padding-pool reason: whoever goes last is the one that reports a
    # shortfall. It also needs the built flevel on disk for `maplist`, which
    # is the field id -> name table its outdoor bitmap is keyed by, so it
    # cannot run before the archives. Does nothing unless Echo-S is in the
    # load order with its "Day Night" option on. See ff7nx_daynight.
    produced += build.apply_daynight(SDOUT_DIR, DUMP, plan, log, produced)
    # ... and the half of it that is a PC executable patch rather than FFNx:
    # the date and the mod's own clock in the main menu, which Echo-S's own
    # Time Keeper tutorial tells you to go and look at. Same option, same
    # pool, and it goes after the cycle so a shortfall lands on the newest
    # thing. See ff7nx_calendar.
    produced += build.apply_calendar(SDOUT_DIR, DUMP, plan, log, produced)
    # BUILD 621. Enhanced Stock UI screens (SEVENTH_NX_ESUI=main): in-place
    # words in exefs/main and data patches in ff7_en. After the calendar,
    # which owns the Time/Gil box when it is installed. See ff7nx_esui.
    produced += build.apply_esui(SDOUT_DIR, DUMP, log, produced)
    # BUILD 680. The camera-locked tunnel attacks (Refuse & co.) cover 16:9:
    # six .data words in ff7_en. See ff7nx_fxtunnel.
    produced += build.apply_fx_tunnel(SDOUT_DIR, DUMP, log, produced)
    # BUILD 629. FFNx's title-screen music (Echo-S's main_menu.ogg): two
    # caves in the title screen's frame function. See ff7nx_titlemusic.
    produced += build.apply_titlemusic(SDOUT_DIR, DUMP, plan, log, produced)
    # BUILD 640. The startup logo movie (Echo-S: _sqlogo with SYW mods,
    # else _eidoslogo; none without Echo-S). See ff7nx_sqlogo.
    produced += build.apply_sqlogo(SDOUT_DIR, DUMP, plan, log, produced)
    # BUILD 641. Nitpick's title background / menu and attract-mode backdrop
    # (module words). See ff7nx_nitpick.
    produced += build.apply_nitpick(SDOUT_DIR, DUMP, log, produced)
    # BUILD 763: the title quad for a non-Nitpick title background.
    produced += build.apply_titleart(SDOUT_DIR, DUMP, log, produced)
    # Echo-S's first field script invokes a PC runtime-selected tutorial
    # movie. This preserves the real materia tutorial and redirects only
    # that field to an otherwise unused movie slot.
    produced += build.apply_echo_tutorial(SDOUT_DIR, DUMP, plan, log, produced)
    # BUILD 525. FFNx's field frame limiter, LAST of the module passes so it
    # builds on everything above; it also rewrites the field frame_time in
    # the ff7_en the 60 FPS pass produced. See ff7nx_fieldpace.
    produced += build.apply_field_pacing(SDOUT_DIR, DUMP, plan, log, produced)
    produced += build.apply_limiter_sleep(SDOUT_DIR, DUMP, plan, log, produced)
    produced += build.apply_boost_ticks(SDOUT_DIR, DUMP, plan, log, produced)
    # BUILD 533. The zero-travel SCR2D rule; see ff7nx_campreserve.
    produced += build.apply_cam_preserve(SDOUT_DIR, DUMP, plan, log, produced)
    # BUILD 610. The camera follows the unfloored position; see ff7nx_campos.
    produced += build.apply_campos(SDOUT_DIR, DUMP, plan, log, produced)
    # BUILD 611. Pointer hand and exit arrows; see ff7nx_pointers.
    produced += build.apply_pointers(SDOUT_DIR, DUMP, plan, log, produced)
    # BUILD 618z. Background-movie fields at 60, the movie at its own rate.
    # It grows BSS, so it must come AFTER the Echo-S voice runtime (whose
    # block has to end in the page the old BSS ended in) -- in build 387 it
    # ran inside apply_field_frame and the voice runtime refused to install.
    # See ff7nx_moviepace.
    produced += build.apply_moviepace(SDOUT_DIR, DUMP, log, produced)
    # BUILD 620j. Layers 3/4 in sub-unit steps (anfrst_4's beams). Grows BSS
    # by 4 bytes, so it sits beside moviepace, after the voice runtime. See
    # ff7nx_subparallax.
    produced += build.apply_subcam(SDOUT_DIR, DUMP, log, produced)
    produced += build.apply_subparallax(SDOUT_DIR, DUMP, log, produced)
    # BUILD 694. Window colour sliders step once per press. Grows BSS by 8
    # bytes, so it sits with the passes above. See ff7nx_winrepeat.
    produced += build.apply_winrepeat(SDOUT_DIR, DUMP, log, produced)
    # BUILD 537. The sound-buffer pool: 32 MB static -> heap, so Cosmo
    # Memory's long SFX loops cannot close the game (Chocobo Race). Last of
    # the cave passes so no shipping cave moves. See ff7nx_audiopool.
    produced += build.apply_audio_pool(SDOUT_DIR, DUMP, log, produced)
    # BUILD 552. GL_LINES 2 px wide (submarine grid). After the audio pool so
    # no shipping cave moves. See ff7nx_sublines.
    produced += build.apply_line_width(SDOUT_DIR, DUMP, log, produced)
    # BUILD 561. Coaster 360 aim. See ff7nx_coaster.
    produced += build.apply_coaster(SDOUT_DIR, DUMP, log, produced)
    # BUILD 565. Fort Condor at 16:9. See ff7nx_condor.
    produced += build.apply_condor(SDOUT_DIR, DUMP, log, produced)
    # BUILD 770. Submarine DEPTH/SPEED gauges toward the 16:9 edges
    # (in-place words only). See ff7nx_subhud.
    produced += build.apply_sub_hud(SDOUT_DIR, DUMP, log, produced)
    # BUILD 567. Per-field 4:3 zoom (convil_2). See ff7nx_fieldzoom.
    produced += build.apply_field_zoom(SDOUT_DIR, DUMP, log, produced)
    produced += build.apply_condorpad(SDOUT_DIR, DUMP, log, produced)
    # BUILD 580. 60 FPS partial field animations. See ff7nx_canim60.
    produced += build.apply_canim60(SDOUT_DIR, DUMP, log, produced)
    # BUILD 585. The world map's far field (the rest of the planet, through
    # the native terrain pipeline). After day/night, whose pass installs the
    # spherical world it needs. See ff7nx_worldfar.
    produced += build.apply_worldfar(SDOUT_DIR, DUMP, log, produced)
    # BUILD 528. Diagnostic frame probe, off unless SEVENTH_NX_FRAME_PROBE=1.
    # Must be the last module pass. See ff7nx_frameprobe.
    # BUILD 665. Field sounds decoded at field entry, and a frame with
    # nothing drawn keeps the last picture. Dead-space parts of their own;
    # the preload reads the sequential-SFX bridge's final route table.
    # See ff7nx_sfxpreload / ff7nx_blankhold.
    produced += build.apply_sfx_preload(SDOUT_DIR, DUMP, plan, log, produced)
    produced += build.apply_blank_hold(SDOUT_DIR, DUMP, plan, log, produced)
    # BUILD 813. audio.fmt read from memory (world-map footstep stalls).
    # See ff7nx_fmtcache.
    produced += build.apply_fmt_cache(SDOUT_DIR, DUMP, plan, log, produced)
    # BUILD 675. FFNx's per-vertex model lighting. See ff7nx_lighting.
    produced += build.apply_lighting(SDOUT_DIR, DUMP, plan, log, produced)
    # BUILD 791. Its per-vertex loop (x86 0x68EA86) native, calling the
    # vertex routine as built above. See ff7nx_lightloop.
    produced += build.apply_lightloop(SDOUT_DIR, DUMP, plan, log, produced)
    # BUILD 792. The background's quad builder (x86 0x6465FB) native. See
    # ff7nx_bgquads.
    produced += build.apply_bgquads(SDOUT_DIR, DUMP, log, produced)
    # BUILD 797. The driver's paletted texture conversion with the palette
    # translated once per texture. See ff7nx_palconv.
    produced += build.apply_palconv(SDOUT_DIR, DUMP, log, produced)
    # BUILD 747. FFNx's effect interpolation (decorated battle effects drawn
    # at 60 Hz). Its own dead translated body + BSS. See ff7nx_fxinterp.
    produced += build.apply_fxinterp(SDOUT_DIR, DUMP, log, produced)
    # BUILD 852. Bahamut ZERO's star field from the live view, every frame.
    # See ff7nx_bzsky.
    produced += build.apply_bzsky(SDOUT_DIR, DUMP, log, produced)
    produced += build.apply_uvwrap(SDOUT_DIR, DUMP, log, produced)
    # The custom PIXEL shader sets (background scaler, FXAA). These touch no
    # module at all, so they can go anywhere -- but they must go BEFORE
    # prune_stale, because that is what deletes them again when the setting
    # goes back to Stock. They also have to land after ff7nx_ws's vertex
    # shaders so both end up in the same romfs/ff7/shaders directory listing.
    produced += build.ff7nx_shaders.apply(SDOUT_DIR, log, produced)
    # BUILD 645/646. Model shadows, battle + field ("Model shadows", default on): after
    # every other module pass and after the shaders above, because it also
    # adds one line to the lmain_vv.glsl they install. See ff7nx_shadows.
    produced += build.apply_shadows(SDOUT_DIR, DUMP, log, produced)
    # BUILD 681. Spit attacks reach the target. After shadows: it takes
    # only padding holes no other pass is left to want. See ff7nx_spitreach.
    produced += build.apply_spit_reach(SDOUT_DIR, DUMP, log, produced)
    # BUILD 682. Refuse's spinning square covers 16:9. See ff7nx_fxplane.
    produced += build.apply_fx_plane(SDOUT_DIR, DUMP, log, produced)
    # BUILD 684. Attacks never draw over the battle UI. See ff7nx_uitop.
    produced += build.apply_ui_top(SDOUT_DIR, DUMP, log, produced)
    # BUILD 809. W-Item duplication / lost first item, in place (no cave).
    produced += build.apply_witem(SDOUT_DIR, DUMP, log, produced)
    # BUILD 860. Autosize dialogue boxes (FFNx's option), off by default.
    # Its own dead translated body, no BSS. See ff7nx_autosize.
    produced += build.apply_text_autosize(SDOUT_DIR, DUMP, log, produced)
    # BUILD 860h. The field depth copy draws with no colour target, not the
    # screen's (mogu_1's water cut at 3/4 width). See ff7nx_depthcopy.
    produced += build.apply_depth_copy(SDOUT_DIR, DUMP, log, produced)
    # BUILD 761. Experimental: 1080p / 900p output when the game starts
    # docked, 720p handheld. Nothing is written while the setting is off.
    # After every feature pass, so each of them places its caves exactly as
    # before; only the diagnostic probe comes later.
    produced += build.apply_dockres(SDOUT_DIR, DUMP, log, produced)
    # BUILD 788. Experimental GL cost cut (SEVENTH_NX_GL_STATE /
    # SEVENTH_NX_GL_UPLOAD): after every feature pass, before the probe, so
    # a probe build times it. See ff7nx_glstate.
    produced += build.apply_glstate(SDOUT_DIR, DUMP, log, produced)
    # BUILD 691. The diagnostic probe (SEVENTH_NX_FRAME_PROBE) is the LAST
    # module pass, after every pass that edits exefs/main. Before 691 it ran
    # ahead of the shadows, spit-reach, fx-plane and UI-on-top passes, so the
    # UI-on-top cave found the probe's wrapper at its call site and refused:
    # a probe build silently tested the battle UI without its fix.
    produced += build.apply_frame_probe(SDOUT_DIR, DUMP, plan, log, produced)

    # Everything the build produces is known now, so anything a PREVIOUS
    # build left here that this one does not produce is stale -- a feature
    # toggled off since. Only ever removes files this packer itself wrote.
    build.prune_stale(SDOUT_DIR, produced, log)

    # BUILD 754: and anything in cache/ that nothing uses any more -- mod
    # extractions whose .iro is gone, the conversion-cache entries it never
    # read for every archive it rebuilt (BUILD 848: per archive), and with
    # Storage > Compact cache the mod extractions. See ff7nx_cachegc.
    try:
        import ff7nx_cachegc
        # BUILD 848: per archive (the set kept), and the opt-in compact.
        ff7nx_cachegc.sweep(MODS_DIR, set(build.ARCHIVES_NOT_REBUILT), log,
                            compact=ff7nx_cachegc.compact_enabled())
    except Exception as exc:                                    # noqa: BLE001
        log('  ! cache cleanup skipped: %s' % exc)

    # BUILD 850: what changed since the last build -- the log says, and
    # sdout_changes/ holds just those files for the SD card.
    try:
        changes = build.sdout_changes(SDOUT_DIR, log=log)
    except Exception as exc:                                    # noqa: BLE001
        changes = None
        log('  ! change list skipped: %s' % exc)

    moved = [name for name, was in audio_before.items()
             if was != _audio_fingerprint().get(name)] if produced else []

    def _closing():
        # BUILD 850: the last lines of the log, after the build time
        log('')
        if produced:
            log(f'done. {len(produced)} files in sdout/')
            _log_sdout_changes(changes, log)
            if moved:
                log('')
                log('!! data/sound CHANGED THIS BUILD (%s). exefs/main '
                    'carries the SFX route table that indexes it, so the two '
                    'are one thing in two files -- copy data/sound as well, '
                    'or the game reads every appended sound variant at the '
                    'wrong offset and they come out as crackle.'
                    % ', '.join(sorted(moved)))
        else:
            log('nothing was written.')

    if callable(getattr(log, 'defer', None)):
        log.defer(_closing)
    else:
        _closing()
    return _finish(bool(produced))


def _log_sdout_changes(changes, log):
    """BUILD 850: tell the user what to copy to the SD card."""
    if not changes or changes['first']:
        log('copy the contents of sdout/ onto the root of your SD card.')
        if changes:
            log('(the next build will list only the files that change, in '
                '%s/)' % build.CHANGES_DIR_NAME)
        return
    added, changed, removed = (changes['added'], changes['changed'],
                               changes['removed'])
    if not (added or changed or removed):
        log('nothing changed since the last build: the SD card is up to date '
            'if you copied that one.')
        return

    def mb(n):
        return ('%.1f GB' % (n / 1e9)) if n >= 1e9 else ('%.1f MB' % (n / 1e6))

    log('changed since the last build: %d new, %d changed, %d removed '
        '(%s to copy).' % (len(added), len(changed), len(removed),
                           mb(changes['bytes'])))
    if added or changed:
        log('copy the contents of %s/ onto the root of your SD card '
            '(replace when asked) -- it holds only these:'
            % build.CHANGES_DIR_NAME)
        items = [('new', r) for r in added] + [('changed', r) for r in changed]
        for tag, rel in items[:12]:
            log('    %-8s %s' % (tag, rel))
        if len(items) > 12:
            log('    ... and %d more (all in %s)'
                % (len(items) - 12, build.CHANGES_LIST_NAME))
    if removed:
        log('delete these from the SD card (this build no longer makes them):')
        for rel in removed[:12]:
            log('    %s' % rel)
        if len(removed) > 12:
            log('    ... and %d more (all in %s)'
                % (len(removed) - 12, build.CHANGES_LIST_NAME))
    log('(skipped copying a build since? copy all of sdout/ once instead.)')


# ------------------------------------------------------------------- UI

def default_globals():
    """The `__global__` part of data/default_settings.json ({} if absent)."""
    try:
        with open(DEFAULT_SETTINGS, encoding='utf-8') as fh:
            return dict(json.load(fh).get('__global__', {}))
    except (OSError, ValueError, AttributeError):
        return {}


def default_mod_settings():
    """{file name: {'enabled', 'options'}} from data/default_mod_settings.json
    ({} if absent). Keyed like settings.json, by the name in mod_sources."""
    try:
        with open(DEFAULT_MOD_SETTINGS, encoding='utf-8') as fh:
            listed = json.load(fh).get('mods', {})
    except (OSError, ValueError, AttributeError):
        return {}
    if not isinstance(listed, dict):
        return {}
    return {name: {'enabled': bool(e.get('enabled', NEW_MOD_ENABLED)),
                   'options': dict(e.get('options') or {})}
            for name, e in listed.items() if isinstance(e, dict)}


def seed_mod_defaults(saved, mods):
    """Give every installed mod that settings.json does not list its factory
    entry, so a supported mod installed later starts on, with the options it
    is tested with, instead of off at the author's defaults. Runs after
    migrate_saved_mod_keys, so a renamed mod keeps its own saved entry.

    Edits `saved` in place (and returns it). A mod settings.json lists is
    never touched, whatever its state."""
    if not isinstance(saved, dict):
        return saved
    factory = default_mod_settings()
    for mod in mods:
        if mod.filename not in saved and mod.filename in factory:
            saved[mod.filename] = factory[mod.filename]
    return saved


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

    saved = build.load_settings(SETTINGS)
    mods = discover_mods(*manual_moves(saved))
    migrate_saved_mod_keys(saved, mods)
    seed_mod_defaults(saved, mods)
    # BUILD 849: mods the user dragged to a new priority (see
    # apply_manual_moves). Highlighted in the list, saved as `mod_moved`.
    moved = set(manual_moves(saved)[1]) & {m.filename for m in mods}
    # BUILD 804: saved values as they are; LOCKED fills what is missing.
    global_saved = nxsettings.effective_global(
        dict(default_globals(), **saved.get('__global__', {})))
    # BUILD 814: Install Mods' check boxes -- mod source id -> selected.
    # A mod not listed is selected (the default), so only the ones the user
    # turned off need to be stored.
    install_sel = {k: bool(v) for k, v in
                   (global_saved.get('install_selection') or {}).items()}
    cap_label_by_value = dict(FIELD_TEX_CAP_CHOICES)
    value_by_cap_label = {v: k for k, v in FIELD_TEX_CAP_CHOICES}
    initial_cap_value = global_saved.get('field_tex_cap', 0)
    if initial_cap_value not in cap_label_by_value:
        initial_cap_value = 0

    world_cap_label_by_value = dict(WORLD_TEX_CAP_CHOICES)
    world_value_by_cap_label = {v: k for k, v in WORLD_TEX_CAP_CHOICES}
    initial_world_cap_value = global_saved.get(
        'world_tex_cap', build.WORLD_TEX_CAP_DEFAULT)
    if initial_world_cap_value not in world_cap_label_by_value:
        initial_world_cap_value = build.WORLD_TEX_CAP_DEFAULT

    bg_cap_label_by_value = dict(BATTLE_BG_TEX_CAP_CHOICES)
    bg_value_by_cap_label = {v: k for k, v in BATTLE_BG_TEX_CAP_CHOICES}
    initial_bg_cap_value = global_saved.get('battle_bg_tex_cap', 256)
    if initial_bg_cap_value not in bg_cap_label_by_value:
        initial_bg_cap_value = 256

    fxcap_label_by_value = dict(SPELL_FX_CAP_CHOICES)
    fxcap_value_by_label = {v: k for k, v in SPELL_FX_CAP_CHOICES}
    initial_fxcap_value = global_saved.get('spell_fx_cap', 0)
    if initial_fxcap_value not in fxcap_label_by_value:
        initial_fxcap_value = 0

    spell_cap_label_by_value = dict(SPELL_TEX_CAP_CHOICES)
    spell_value_by_cap_label = {v: k for k, v in SPELL_TEX_CAP_CHOICES}
    initial_spell_cap_value = global_saved.get('spell_tex_cap', 512)
    if initial_spell_cap_value not in spell_cap_label_by_value:
        initial_spell_cap_value = 512

    mini_cap_label_by_value = dict(MINIGAME_TEX_CAP_CHOICES)
    mini_value_by_cap_label = {v: k for k, v in MINIGAME_TEX_CAP_CHOICES}
    initial_mini_cap_value = global_saved.get(
        'minigame_tex_cap', build.ff7nx_minigametex.DEFAULT_CAP)
    if initial_mini_cap_value not in mini_cap_label_by_value:
        initial_mini_cap_value = build.ff7nx_minigametex.DEFAULT_CAP

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

    fd1_label_by_value = dict(FIELD_BG_D1_PX_CHOICES)
    fd1_value_by_label = {v: k for k, v in FIELD_BG_D1_PX_CHOICES}
    initial_fd1_value = global_saved.get(
        'field_bg_d1_px', build.ff7nx_fieldbg.VANILLA_D1_PX)
    if initial_fd1_value not in fd1_label_by_value:
        initial_fd1_value = build.ff7nx_fieldbg.VANILLA_D1_PX

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
    # BUILD 834: the same sizes and faces as macOS, which the whole layout
    # is designed on. Tk sizes fonts in POINTS: macOS draws 1 pt as 1 px,
    # Windows and Linux as 96/72 = 1.33 px, so every font -- and everything
    # measured from one, check boxes and row heights included -- came out a
    # third larger there. Scaling 1.0 is macOS's own value. Windows still
    # enlarges the whole window for a 125%/150% display setting, as it does
    # every app that does not declare its own DPI handling, so the layout
    # keeps its macOS proportions at any setting.
    # Helvetica and Menlo are macOS fonts; elsewhere Tk would substitute on
    # its own (Menlo usually becomes Courier), so name the nearest match:
    # Arial shares Helvetica's metrics, Consolas is Windows' Menlo.
    UI_FONT, MONO_FONT = 'Helvetica', 'Menlo'
    if sys.platform != 'darwin':
        try:
            root.tk.call('tk', 'scaling', 1.0)
        except tk.TclError:
            pass
        try:
            _fams = {f.lower(): f for f in tkfont.families(root)}
        except tk.TclError:
            _fams = {}

        def _pick(*names):
            return next((_fams[n.lower()] for n in names
                         if n.lower() in _fams), names[0])
        UI_FONT = _pick('Helvetica', 'Arial', 'Liberation Sans',
                        'Nimbus Sans', 'Nimbus Sans L', 'Arimo',
                        'Helvetica Neue', 'DejaVu Sans')
        MONO_FONT = _pick('Menlo', 'Consolas', 'DejaVu Sans Mono',
                          'Liberation Mono', 'Cascadia Mono', 'Courier New')
        # the defaults Tk's own widgets use (entries, menus, dialogs), at
        # the sizes macOS's Tk gives them
        for _name, _size in (('TkDefaultFont', 13), ('TkTextFont', 13),
                             ('TkMenuFont', 13), ('TkHeadingFont', 13),
                             ('TkCaptionFont', 13), ('TkTooltipFont', 11)):
            try:
                tkfont.nametofont(_name).configure(family=UI_FONT,
                                                   size=_size)
            except tk.TclError:
                pass
        try:
            tkfont.nametofont('TkFixedFont').configure(family=MONO_FONT,
                                                       size=11)
        except tk.TclError:
            pass
    # Default layout (Patrick's, Oct 2026): 1090x1128 with the Mods column
    # at ~42.5% of the width and the log at the bottom ~26% (WIN_SPLIT_*).
    # On a smaller screen the window shrinks to fit, either way, and the
    # splits stay proportional because they are fractions.
    WIN_W, WIN_H = 1090, 1128
    WIN_SPLIT_X, WIN_SPLIT_Y = 0.425, 0.739
    _sw, _sh = root.winfo_screenwidth(), root.winfo_screenheight()
    _ww, _wh = min(WIN_W, _sw - 40), min(WIN_H, _sh - 100)
    root.geometry('%dx%d+%d+%d' % (_ww, _wh, max(0, (_sw - _ww) // 2),
                                   max(30, (_sh - _wh) // 2 - 20)))
    root.minsize(min(880, _ww), min(600, _wh))

    # ---------- theme ---------------------------------------------------
    # The palette comes from nxthemes (Settings > GUI), read once
    # here; a change applies on the next launch.
    #
    # This app always renders its own dark theme, regardless of the OS's
    # own light/dark setting. 'aqua' (macOS's native ttk theme) was the
    # source of the jarring white-selection-bar bug: aqua draws its own
    # native chrome per-widget and mostly ignores colors we set on it, so a
    # hardcoded "light blue" selection color rendered as a stark white/pale
    # bar with no way to fix it from here. 'clam' is fully style-driven --
    # nothing native, no OS-dependent surprises -- so every color below is
    # exactly what renders, on every platform, every time.
    _theme = nxthemes.palette(_global_setting('gui_theme', nxthemes.DEFAULT))
    # The names below are read when something is drawn, never copied, so
    # apply_theme can rebind them and repaint without a restart.
    BG_APP        = _theme['BG_APP']        # window + all panel backgrounds
    BG_ROW        = _theme['BG_ROW']        # unselected mod-row background
    BG_ROW_HOVER  = _theme['BG_ROW_HOVER']  # mod-row background on mouse-over
    BG_ROW_SEL    = _theme['BG_ROW_SEL']    # selected mod-row background
    BORDER        = _theme['BORDER']
    ACCENT        = _theme['ACCENT']
    ACCENT_HOVER  = _theme['ACCENT_HOVER']
    ACCENT_SOFT   = _theme['ACCENT_SOFT']
    ON_ACCENT     = _theme['ON_ACCENT']     # text on ACCENT / ACCENT_HOVER
    CHECK_TICK    = _theme['CHECK_TICK']
    TEXT_PRIMARY  = _theme['TEXT_PRIMARY']
    TEXT_SECONDARY = _theme['TEXT_SECONDARY']
    TEXT_MUTED    = _theme['TEXT_MUTED']
    ARCHIVE_EXACT = ACCENT_SOFT
    ARCHIVE_EST   = TEXT_MUTED
    LOG_BG        = _theme['LOG_BG']
    LOG_TEXT      = _theme['LOG_TEXT']
    GOOD          = _theme['GOOD']
    WARN          = _theme['WARN']

    style = ttk.Style()
    try:
        style.theme_use('clam')
    except tk.TclError:
        pass

    def _apply_styles():
        root.configure(bg=BG_APP)
        style.configure('.', background=BG_APP, foreground=TEXT_PRIMARY,
                        fieldbackground=BG_ROW, bordercolor=BORDER,
                        darkcolor=BG_APP, lightcolor=BG_APP,
                        troughcolor=BG_ROW, focuscolor=ACCENT,
                        font=(UI_FONT, 11))
        style.configure('TFrame', background=BG_APP)
        style.configure('TLabel', background=BG_APP, foreground=TEXT_PRIMARY)
        style.configure('TLabelframe', background=BG_APP, bordercolor=BORDER,
                        relief='solid', borderwidth=1)
        style.configure('TLabelframe.Label', background=BG_APP,
                        foreground=TEXT_SECONDARY, font=(UI_FONT, 10, 'bold'))
        style.configure('TPanedwindow', background=BG_APP)
        style.configure('TSeparator', background=BORDER)

        style.configure('Header.TLabel', font=(UI_FONT, 18, 'bold'),
                        foreground=TEXT_PRIMARY)
        style.configure('Sub.TLabel', foreground=TEXT_SECONDARY)
        # BUILD 814b: an Install Mods row whose check box is off
        style.configure('GetModOff.TLabel', foreground=TEXT_MUTED)
        style.configure('ModName.TLabel', font=(UI_FONT, 12),
                        background=BG_ROW, foreground=TEXT_PRIMARY)
        style.configure('ModNameSelected.TLabel', font=(UI_FONT, 12, 'bold'),
                        background=BG_ROW_SEL, foreground=ACCENT_SOFT)
        style.configure('Link.TLabel', foreground=ACCENT_SOFT,
                        font=(UI_FONT, 11, 'underline'))
        style.configure('ColHeader.TLabel', font=(UI_FONT, 9, 'bold'),
                        foreground=TEXT_MUTED)
        style.configure('Archive.TLabel', font=(UI_FONT, 11),
                        foreground=ARCHIVE_EXACT)
        style.configure('ArchiveEst.TLabel', font=(UI_FONT, 11),
                        foreground=ARCHIVE_EST)

        # Settings' checkboxes. Without these clam paints a light grey box behind
        # a disabled or hovered one, and a white indicator in every theme.
        style.configure('TCheckbutton', background=BG_APP,
                        foreground=TEXT_PRIMARY, indicatorbackground=BG_ROW,
                        indicatorforeground=CHECK_TICK,
                        upperbordercolor=TEXT_SECONDARY,
                        lowerbordercolor=TEXT_SECONDARY)
        style.map('TCheckbutton',
                  background=[('active', BG_APP), ('disabled', BG_APP)],
                  foreground=[('disabled', TEXT_MUTED)],
                  indicatorbackground=[('disabled', BG_APP),
                                       ('selected', ACCENT)],
                  upperbordercolor=[('disabled', TEXT_MUTED),
                                    ('selected', ACCENT)],
                  lowerbordercolor=[('disabled', TEXT_MUTED),
                                    ('selected', ACCENT)])

        style.configure('TButton', background=BG_ROW, foreground=TEXT_PRIMARY,
                        bordercolor=BORDER, padding=(10, 6))
        style.map('TButton',
                  background=[('active', BG_ROW_HOVER), ('disabled', BG_APP)],
                  foreground=[('disabled', TEXT_MUTED)])
        style.configure('Build.TButton', font=(UI_FONT, 13, 'bold'),
                        padding=(18, 9), background=ACCENT, foreground=ON_ACCENT,
                        bordercolor=ACCENT)
        style.map('Build.TButton',
                  background=[('active', ACCENT_HOVER), ('disabled', BG_ROW)],
                  foreground=[('disabled', TEXT_MUTED)])
        # BUILD 804: an accent button at the normal button size (About Dev)
        style.configure('Accent.TButton', background=ACCENT,
                        foreground=ON_ACCENT, bordercolor=ACCENT)
        style.map('Accent.TButton',
                  background=[('active', ACCENT_HOVER), ('disabled', BG_ROW)],
                  foreground=[('disabled', TEXT_MUTED)])

        style.configure('TCombobox', fieldbackground=BG_ROW, background=BG_ROW,
                        foreground=TEXT_PRIMARY, arrowcolor=TEXT_SECONDARY,
                        bordercolor=BORDER, selectbackground=BG_ROW,
                        selectforeground=TEXT_PRIMARY)
        style.map('TCombobox',
                  fieldbackground=[('disabled', BG_APP), ('readonly', BG_ROW), ('focus', BG_ROW)],
                  foreground=[('disabled', TEXT_MUTED), ('readonly', TEXT_PRIMARY)],
                  background=[('readonly', BG_ROW)],
                  bordercolor=[('focus', ACCENT)])
        # The Combobox's dropdown list is a separate, un-styled Tk Listbox --
        # without this it stays white-on-black regardless of the ttk style set
        # above, which would be its own jarring mismatch against a dark app.
        root.option_add('*TCombobox*Listbox.background', BG_ROW)
        root.option_add('*TCombobox*Listbox.foreground', TEXT_PRIMARY)
        root.option_add('*TCombobox*Listbox.selectBackground', ACCENT)
        root.option_add('*TCombobox*Listbox.selectForeground', ON_ACCENT)
        root.option_add('*TCombobox*Listbox.font', (UI_FONT, 11))

        for pbstyle in ('TProgressbar', 'Horizontal.TProgressbar'):
            style.configure(pbstyle, background=ACCENT, troughcolor=BG_ROW,
                            bordercolor=BORDER, lightcolor=ACCENT,
                            darkcolor=ACCENT)

        style.configure('Vertical.TScrollbar', background=BORDER,
                        troughcolor=BG_APP, bordercolor=BG_APP,
                        arrowcolor=TEXT_SECONDARY, relief='flat', gripcount=0)
        style.map('Vertical.TScrollbar',
                  background=[('active', ACCENT), ('pressed', ACCENT)])

    _apply_styles()

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
    ICON_PATH = os.path.join(nxpaths.APP, 'assets', 'icon.png')
    _icon_keep = []

    def _load_icon(px=None, path=ICON_PATH):
        """`path` (icon.png) as a Tk image, resampled to `px` square (None =
        native)."""
        if not os.path.exists(path):
            return None
        img = None
        try:
            # preferred: a real resample
            from PIL import Image, ImageTk
            im = Image.open(path).convert('RGBA')
            if px and im.size != (px, px):
                im = im.resize((px, px), getattr(Image, 'LANCZOS',
                                                 Image.BICUBIC))
            img = ImageTk.PhotoImage(im)
        except Exception:
            # Pillow can be installed without ImageTk (separate package on
            # Linux), so fall back to Tk's own PNG reader. It can only
            # downscale by an integer factor, which is fine on a square source.
            try:
                raw = tk.PhotoImage(file=path)
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
    #
    # On macOS Tk's iconphoto IS the Dock icon, so it gets the rounded-square
    # icon_macos.png too. Given the round icon.png, the release's Dock icon
    # flashed round for a moment at launch, between the .app's own icon and
    # the AppKit call below.
    _dock_icon = os.path.join(nxpaths.APP, 'assets', 'icon_macos.png')
    _win_icon = _load_icon(256, _dock_icon if sys.platform == 'darwin'
                           and os.path.exists(_dock_icon) else ICON_PATH)
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
    #
    # The Dock gets icon_macos.png, the rounded-square cut of the same art
    # that the release's .app carries: macOS 26 shows any other shape on a
    # grey plate, and a Dock icon that changed shape at launch looks wrong.
    if sys.platform == 'darwin':
        try:
            from AppKit import NSApplication, NSImage
            from Foundation import NSURL
            ns = NSImage.alloc().initWithContentsOfURL_(
                NSURL.fileURLWithPath_(_dock_icon
                                       if os.path.exists(_dock_icon)
                                       else ICON_PATH))
            if ns is not None:
                NSApplication.sharedApplication().setApplicationIconImage_(ns)
        except Exception:
            pass

    _icon = _load_icon(36)
    if _icon is not None:
        ttk.Label(header, image=_icon).pack(side='left', padx=(0, 10))

    ttk.Label(header, text='7th Heaven NX', style='Header.TLabel'
              ).pack(side='left')
    def _count_text():
        return f'v{VERSION}  ·  {len(mods)} mods  ·'

    count_label = ttk.Label(header, text=_count_text(), style='Sub.TLabel')
    count_label.pack(side='left', padx=(12, 0))
    # Dump status. Checked here (build.check_game_dump reads headers, the
    # archives' directories and one small hash, ~0.1 s) and again whenever
    # the refresh icon beside it is clicked, so a dump added while the
    # program is open is picked up without a restart. Not found: click the
    # label to open the folder the dump goes in. Invalid: click to put the
    # reasons in the log.
    # set while a build runs: the dump must not change under it
    build_busy = {'active': False}
    dump_state = {'problems': []}
    dump_lbl = ttk.Label(header, text='')
    dump_lbl.pack(side='left', padx=(4, 0))

    def _dump_status():
        dump_state['problems'] = build.check_game_dump(DUMP)
        if DUMP is None:
            text, fg = 'game dump not found', WARN
        elif dump_state['problems']:
            text, fg = 'game dump found - INVALID', WARN
        else:
            text, fg = 'game dump found - VALID', GOOD
        dump_lbl.configure(text=text, foreground=fg)

    _dump_status()

    def _refresh_image():
        """A circular arrow drawn with Pillow (4x supersampled), so it looks
        the same on every platform instead of depending on a font having
        the glyph. None without ImageTk; the caller falls back to text.

        The image is exactly as tall as the label's line (ascent + descent)
        and the arrow is centred on the middle of the CAP height inside it,
        not the middle of the image. Tk centres both labels on their line
        box, so with equal heights the arrow lands on the letters' visual
        centre whatever font the platform uses."""
        try:
            from PIL import Image, ImageDraw, ImageTk
        except Exception:
            return None
        import math
        try:
            f = tkfont.Font(font=dump_lbl.cget('font') or
                            ttk.Style().lookup('TLabel', 'font') or
                            'TkDefaultFont')
            asc, desc = f.metrics('ascent'), f.metrics('descent')
            act = f.actual()
            cap = _cap_height((act['family'], abs(int(act['size'])) or 11),
                              asc, desc)
        except Exception:                                  # noqa: BLE001
            asc, desc, cap = 14, 4, 10
        ss = 4
        n = max(12, cap + 3)                    # the arrow's own size
        W, H = n, asc + desc
        # draw the arrow on its own square, then crop to its ink
        S = n * ss
        art = Image.new('RGBA', (S, S), (0, 0, 0, 0))
        d = ImageDraw.Draw(art)
        col = TEXT_SECONDARY
        pad, w = 2 * ss, int(1.6 * ss)
        d.arc([pad, pad, S - pad, S - pad], start=-60, end=250,
              fill=col, width=w)
        cx = cy = S / 2.0
        rad = (S - 2 * pad) / 2.0
        ang = math.radians(-60)
        tx, ty = cx + rad * math.cos(ang), cy + rad * math.sin(ang)
        h = 3.2 * ss
        d.polygon([(tx + h, ty - h * 0.2), (tx - h * 0.6, ty - h * 0.9),
                   (tx - h * 0.1, ty + h * 0.9)], fill=col)
        box = art.getbbox()
        if box:
            art = art.crop(box)
        # centre the ink on the cap band: its middle is at asc - cap/2
        im = Image.new('RGBA', (W * ss, H * ss), (0, 0, 0, 0))
        mid = (asc - cap / 2.0) * ss
        im.paste(art, (int(round((W * ss - art.width) / 2.0)),
                       int(round(mid - art.height / 2.0))))
        return ImageTk.PhotoImage(im.resize((W, H), Image.LANCZOS))

    dump_refresh = ttk.Label(header, padding=0)
    dump_refresh.pack(side='left', padx=(6, 0))

    def _redraw_refresh():
        img = _refresh_image()
        if img is not None:
            dump_refresh.configure(image=img, text='')
            dump_refresh.image = img
        else:
            dump_refresh.configure(text='\u21bb', foreground=TEXT_SECONDARY)

    # after the window is built: _cap_height is defined further down
    dump_refresh.configure(text='\u21bb', foreground=TEXT_SECONDARY)
    root.after_idle(_redraw_refresh)

    def _dump_refresh(_evt=None):
        """Look for the dump again, as at startup."""
        global DUMP, WORKINGDIR
        if build_busy['active']:
            log_write('Wait for the build to finish before checking the '
                      'game dump again.')
            return 'break'
        DUMP = build.find_game_dump(HERE)
        WORKINGDIR = (DUMP.workingdir if DUMP
                      else os.path.join(HERE, 'workingdir'))
        _dump_status()
        # the "modifies:" notes read archive names from the dump
        catalogs.clear()
        if os.path.isdir(WORKINGDIR):
            try:
                catalogs.update(build.load_catalogs(WORKINGDIR)[0])
            except Exception:                              # noqa: BLE001
                pass
        if DUMP is None:
            log_write('Game dump: still not found in %s'
                      % os.path.join(HERE, 'dump'))
        elif dump_state['problems']:
            log_write('Game dump at %s is not usable yet:' % DUMP.root)
            for why in dump_state['problems']:
                log_write('  ! ' + why)
        else:
            log_write('Game dump at %s: valid.' % DUMP.root)
        return 'break'

    dump_refresh.bind('<Button-1>', _dump_refresh)

    def _dump_click(_evt=None):
        """Opens the dump folder, whatever its state; says why when it is
        missing or not right."""
        if DUMP is None:
            target = os.path.join(HERE, 'dump')
            os.makedirs(target, exist_ok=True)
            log_write('Put your Switch game dump in %s (romfs/ and exefs/ '
                      'inside it), then press the refresh icon.' % target)
        else:
            target = DUMP.root
            if dump_state['problems']:
                log_write('Game dump at %s:' % DUMP.root)
                for why in dump_state['problems']:
                    log_write('  ! ' + why)
        try:
            _reveal(target)
        except OSError:             # no file browser to hand it to
            pass

    dump_lbl.configure(cursor='')
    dump_lbl.bind('<Button-1>', _dump_click)
    dump_lbl.bind('<Enter>', lambda _e: dump_lbl.configure(
        font=(UI_FONT, 11, 'underline')))
    dump_lbl.bind('<Leave>', lambda _e: dump_lbl.configure(
        font=(UI_FONT, 11)))
    # short: the header buttons are packed after this and get only the
    # width that is left, so a long label clips "Install Mods"
    # BUILD 814: the "·  DEV" tag beside this label is gone -- it no longer
    # told users anything (Developer Settings only shows or hides rows since
    # BUILD 804). Kept as a no-op: the Settings dialog still calls it.
    def _show_dev_tag():
        pass

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

    world_cap_var = tk.StringVar(
        value=world_cap_label_by_value[initial_world_cap_value])

    def current_world_tex_cap():
        return world_value_by_cap_label.get(
            world_cap_var.get(), build.WORLD_TEX_CAP_DEFAULT)

    bg_cap_var = tk.StringVar(value=bg_cap_label_by_value[initial_bg_cap_value])

    def current_battle_bg_tex_cap():
        return bg_value_by_cap_label.get(bg_cap_var.get(), 256)

    spell_cap_var = tk.StringVar(
        value=spell_cap_label_by_value[initial_spell_cap_value])

    fxcap_var = tk.StringVar(value=fxcap_label_by_value[initial_fxcap_value])

    def current_spell_fx_cap():
        return fxcap_value_by_label.get(fxcap_var.get(), 0)

    def current_spell_tex_cap():
        return spell_value_by_cap_label.get(spell_cap_var.get(), 512)

    _dr = global_saved.get('dock_resolution', DOCK_RES_DEFAULT)
    dockres_var = tk.StringVar(value=DOCK_RES_LABELS.get(
        DOCK_RES_DEFAULT if _dr is None else str(_dr),
        DOCK_RES_LABELS[DOCK_RES_DEFAULT]))

    title_var = tk.StringVar(value=TITLE_SCREEN_LABELS.get(
        str(global_saved.get('title_screen', TITLE_SCREEN_DEFAULT)),
        TITLE_SCREEN_LABELS[TITLE_SCREEN_DEFAULT]))

    def current_title_screen():
        return TITLE_SCREEN_BY_LABEL.get(title_var.get(), TITLE_SCREEN_DEFAULT)

    theme_var = tk.StringVar(value=nxthemes.LABELS.get(
        str(global_saved.get('gui_theme', nxthemes.DEFAULT)),
        nxthemes.LABELS[nxthemes.DEFAULT]))

    def current_theme():
        return nxthemes.BY_LABEL.get(theme_var.get(), nxthemes.DEFAULT)

    # Settings > GUI > Developer Settings. SEVENTH_NX_DEV=1 forces it on.
    devset_var = tk.BooleanVar(value=nxsettings.dev_mode())

    def current_dock_resolution():
        return DOCK_RES_BY_LABEL.get(dockres_var.get(), DOCK_RES_DEFAULT)

    _esc = global_saved.get('esui_texscale', 2)
    esuisc_var = tk.StringVar(value=ESUI_TEXSCALE_LABELS.get(
        _esc, ESUI_TEXSCALE_LABELS[2]))

    def current_esui_texscale():
        return ESUI_TEXSCALE_BY_LABEL.get(esuisc_var.get(), 2)

    mini_cap_var = tk.StringVar(
        value=mini_cap_label_by_value[initial_mini_cap_value])

    def current_minigame_tex_cap():
        return mini_value_by_cap_label.get(
            mini_cap_var.get(), build.ff7nx_minigametex.DEFAULT_CAP)

    fbg_var = tk.StringVar(value=fbg_label_by_value[initial_fbg_value])

    def current_field_bg_page_px():
        return fbg_value_by_label.get(fbg_var.get(), 0)

    ftc_var = tk.StringVar(value=ftc_label_by_value[initial_ftc_value])

    def current_field_bg_truecolor():
        return ftc_value_by_label.get(
            ftc_var.get(), build.field_bg_dense.MAX_TRUECOLOR_PAGES)

    fd1_var = tk.StringVar(value=fd1_label_by_value[initial_fd1_value])

    def current_field_bg_d1_px():
        return fd1_value_by_label.get(fd1_var.get(),
                                      build.ff7nx_fieldbg.VANILLA_D1_PX)

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
    import ff7nx_texcache as _texcache
    _tc_saved = str(global_saved.get('texture_cache', _texcache.MODE))
    if _tc_saved not in TEX_CACHE_LABELS:
        # Migrates the old boolean, and survives a settings.json written by
        # any build that predates the three-way choice.
        _tc_saved = ('nocache' if global_saved.get('no_texture_cache')
                     else _texcache.MODE)
    texcache_var = tk.StringVar(value=TEX_CACHE_LABELS[_tc_saved])

    def current_texture_cache():
        return TEX_CACHE_BY_LABEL.get(texcache_var.get(), _texcache.MODE)
    # REMOVED: bgclr_var ('bg_clear', "Black 16:9 margins") and mclip_var
    # ('movie_clip', "Clip models to the movie"). Both retired -- FINDINGS-92
    # §6. A stale key left in settings.json by an older build is ignored: the
    # environment variables are no longer written and nothing reads them.
    #
    # The 16:9 field frame. Defaults ON because with widescreen off it cannot
    # write a word -- ff7nx_letterbox.enabled() and ff7nx_modelcull.enabled()
    # both fall through to ff7nx_ws.enabled().
    frame_var = tk.BooleanVar(value=bool(global_saved.get('field_frame', True)))
    # BUILD 645/646. Model shadows (battle + field); on by default, off writes nothing.
    shadows_var = tk.BooleanVar(value=bool(global_saved.get('model_shadows', True)))
    nocheat_var = tk.BooleanVar(value=bool(global_saved.get('no_cheats', True)))   # BUILD 819: on by default
    # BUILD 859: Controls > W-Item bug fix (ff7nx_witem), on by default; off
    # keeps the original game's W-Item duplication
    witem_var = tk.BooleanVar(value=bool(global_saved.get('witem_fix', True)))
    # BUILD 859: Voice > Advanced facial animations (ff7nx_facial's runtime:
    # expressions and the voice lip flap), on by default
    facial_var = tk.BooleanVar(value=bool(global_saved.get('advanced_facial',
                                                          True)))
    # BUILD 860: Display > Autosize dialogue boxes (ff7nx_autosize, FFNx's
    # ff7_field_autosize_text_box), off by default as in FFNx
    autosize_var = tk.BooleanVar(value=bool(global_saved.get('text_autosize',
                                                            False)))
    # BUILD 848: Storage > Compact cache after build (ff7nx_cachegc); off by default.
    compact_var = tk.BooleanVar(value=bool(global_saved.get('compact_cache', False)))

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
    # BUILD 757: 4.5 is a valid step, so not int().
    fbuf_saved = build.ff7nx_fieldbuf._norm(fbuf_saved)
    if fbuf_saved is None:
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

    # The graphics pool. Same shape as the field buffer above: an unknown
    # saved value falls back to the largest offered entry that is not
    # greater than it, never silently to something bigger than what was
    # saved -- a memory reservation must not grow behind your back.
    import ff7nx_gfxpool as _gfxpool
    gfx_saved = global_saved.get('gfx_pool_mb', _gfxpool.DEFAULT_MB)
    try:
        gfx_saved = int(gfx_saved)
    except (TypeError, ValueError):
        gfx_saved = _gfxpool.DEFAULT_MB
    if gfx_saved not in GFX_POOL_LABELS:
        gfx_saved = max((v for v in GFX_POOL_LABELS if v <= gfx_saved),
                        default=_gfxpool.STOCK_MB)
    gfx_var = tk.StringVar(value=GFX_POOL_LABELS[gfx_saved])

    def current_gfx_pool():
        return GFX_POOL_BY_LABEL.get(gfx_var.get(), _gfxpool.DEFAULT_MB)

    voice_norm_saved = global_saved.get(
        'voice_normalize', build.voice_ogg.NORMALIZE_DEFAULT)
    if voice_norm_saved not in VOICE_NORM_LABELS:
        voice_norm_saved = build.voice_ogg.NORMALIZE_DEFAULT
    voice_norm_var = tk.StringVar(value=VOICE_NORM_LABELS[voice_norm_saved])

    def current_voice_normalize():
        return VOICE_NORM_BY_LABEL.get(voice_norm_var.get(),
                                       build.voice_ogg.NORMALIZE_DEFAULT)

    scaler_saved = global_saved.get('scaler', '')
    if scaler_saved not in SCALER_LABELS:
        scaler_saved = ''
    scaler_var = tk.StringVar(value=SCALER_LABELS[scaler_saved])

    def current_scaler():
        return SCALER_BY_LABEL.get(scaler_var.get(), '')

    _bp = global_saved.get('hd_black_point',
                           build.ff7nx_shaders.BLACK_POINT_DEFAULT)
    if _bp not in BLACK_POINT_LABELS:
        _bp = build.ff7nx_shaders.BLACK_POINT_DEFAULT
    bp_var = tk.StringVar(value=BLACK_POINT_LABELS[_bp])

    def current_black_point():
        return BLACK_POINT_BY_LABEL.get(
            bp_var.get(), build.ff7nx_shaders.BLACK_POINT_DEFAULT)

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

    # fps_60, movie_30fps, analog_360, no_autorun, no_cheats and the limiter
    # need exefs/main, which a bare workingdir/ (or no dump yet) does not
    # have. They stay editable and saved as chosen -- a fresh release folder
    # has no dump until the user adds one, and forcing them off here used to
    # save that over their choice -- and only the BUILD treats them as off
    # (`_needs_nso`). 16:9 is not one of them: the content stage edits
    # flevel.lgp only, so it works on a workingdir-only setup.
    def _needs_nso(value, off=False):
        return value if have_nso else off

    # ---- the dialog -----------------------------------------------------
    # BUILD 756. Two lists. USER_SECTIONS is all a player sees: settings that
    # are a matter of taste and cannot break the build. DEV_SECTIONS appears
    # only in developer mode, but builds as saved either way (BUILD 804). Help text is kept to a line or two; the
    # full measurements and history that used to live here are in
    # docs/notes/SETTINGS-HELP-ARCHIVE-755.md.
    #
    # Row: (kind, label, var, values, help, needs_full_dump)
    class _Choices(list):
        """Every label; `.user` is the list without the developer-only
        ones, which is what the dialog offers outside developer mode."""

    def _choices(pairs, user_hide=()):
        out = _Choices(l for _v, l in pairs)
        out.user = [l for v, l in pairs if v not in user_hide]
        return out

    USER_SECTIONS = [
        ('GUI', [
            ('combo', 'Theme', theme_var,
             [label for _k, label in nxthemes.CHOICES],
             'Colours of this window. Has no effect on the build.', False),
            ('check', 'Developer Settings', devset_var, None,
             'Shows every setting. Turning it off only hides them: the '
             'build keeps using them as set. The note on the right says '
             'whether they are at their tested defaults.', False),
        ]),
        ('Display', [
            ('combo', 'Aspect ratio', ws_var,
             _choices(WIDESCREEN_CHOICES, user_hide=('ws',)),
             'Widens the picture to 16:9. Needs a widescreen mod such as '
             'Cosmos Limit Break.', True),
            ('combo', 'Output resolution', dockres_var,
             _choices(DOCK_RES_CHOICES, user_hide=('900',)),
             'Always: 1080p on the TV; the console scales it to the handheld '
             'screen, so docking and undocking work at any time. Dynamic: '
             'chosen when the game starts. The game renders at the same '
             'internal size in every option.', True),
            ('combo', 'Title screen', title_var,
             _choices(TITLE_SCREEN_CHOICES),
             'Buster Sword: a hand-refined HD painting of the sword, where '
             'the original title has it. As the mods set it: what 7th Heaven '
             'would show.', True),
            ('check', 'Autosize dialogue boxes', autosize_var, None,
             'Sizes each dialogue box to its text when it opens, as FFNx\'s '
             'option of the same name: no wide boxes around short lines, no '
             'long lines running into the border. Boxes never get taller '
             'than the game made them.', True),
        ]),
        ('Graphics', [
            ('combo', 'Background upscaling', scaler_var,
             _choices(SCALER_CHOICES, user_hide=('soft',)),
             'How the pre-rendered backgrounds are scaled up to the screen. '
             'HD is tuned for this game.', False),
            ('combo', 'Anti-aliasing', fxaa_var,
             _choices(FXAA_CHOICES),
             'Smooths the edges of 3D models. Off is sharper but more '
             'jagged.', False),
            ('combo', 'Movie shader', vshader_var,
             _choices(VIDEO_SHADER_CHOICES),
             'How FMVs are drawn. HD is sharper.', False),
            ('combo', 'Movie quality', movie_var,
             _choices(MOVIE_QUALITY_CHOICES),
             'Higher quality makes the build slower and the SD card output '
             'larger.', False),
            ('check', 'Model shadows', shadows_var, None,
             'Characters, enemies and vehicles cast shadows shaped like '
             'themselves instead of round blobs.', True),
        ]),
        ('Controls', [
            ('check', '360° movement', a360_var, None,
             'Walk in the exact direction of the stick instead of eight '
             'fixed directions.', True),
            ('check', 'Disable auto-run', norun_var, None,
             'Pushing the stick all the way no longer makes you run. Hold '
             'the run button instead.', True),
            ('check', 'Disable the right-stick cheat', nocheat_var, None,
             'Clicking the right stick no longer refills HP, MP and Limit. '
             'Stops one accidental press spoiling a playthrough.', True),
            ('check', 'W-Item bug fix', witem_var, None,
             'Fixes the W-Item bug: cancelling the second pick no longer '
             'duplicates the first item, and backing out no longer loses it. '
             'Turn off to keep the original game\'s duplication glitch.',
             True),
        ]),
        ('Storage', [
            ('check', 'Compact cache after build', compact_var, None,
             'Deletes the unpacked copies of your mods (about 15 GB with the '
             'full mod list) once a build has finished. The next build '
             'unpacks them again from the .iro files, which takes a few '
             'minutes longer; nothing else is rebuilt.', False),
        ]),
        ('Voice', [
            ('combo', 'Dialogue loudness', voice_norm_var,
             _choices(VOICE_NORM_CHOICES, user_hide=('loudness:-15.3', 'peak:-1.1')),
             'Evens out Echo-S voice lines so quiet and loud ones sound '
             'alike. Changing it re-encodes the voice clips once.', False),
            ('check', 'Advanced facial animations', facial_var, None,
             'Characters blink, change expression and move their mouths '
             'while a voiced line plays. Off keeps the original game\'s '
             'simple blink.', True),
        ]),
    ]

    DEV_SECTIONS = [
        ('Developer — frame rate', [
            ('check', '60/30 FPS Patches', fps_var, None,
             'The frame-rate set for menus, battles, field and world. '
             'Follows the 60/30 FPS Gameplay mod: Full 60 FPS runs '
             'everything at 60, Full 30 FPS runs battles at 30 and leaves '
             'the field and world at 30, Original FPS turns it off.', True),
            ('check', '30 FPS FMVs', m30_var, None,
             'Builds every movie at 30 fps and halves the movie frame '
             'counter to match.', True),
            ('combo', '60 FPS frame pacing headroom', lim_var,
             _choices(LIMITER_FPS_CHOICES),
             'The limiters run slightly under 60. Use the lowest target that '
             'holds a solid 60; higher runs the game fast. Full 30 FPS '
             'ignores it and aims for 30.', True),
        ]),
        ('Developer — rendering and memory', [
            ('combo', 'Field render resolution', fbuf_var,
             _choices(FIELD_BUF_CHOICES),
             'Size of the field render target. Off shows vertical bands in '
             '16:9; 2× and 3× cost graphics memory. HANDOFF-51.',
             True),
            ('combo', 'Graphics memory', gfx_var,
             _choices(GFX_POOL_CHOICES),
             'The driver pool. Keep 256 MB: larger corrupts textures, smaller '
             'is a diagnostic. FINDINGS-303.', True),
            ('check', 'Full-height 16:9 field', frame_var, None,
             'Removes the field’s 32-unit letterbox, shifts the FMV quad '
             'to match and paints the FMV margins. 16:9 only. FINDINGS-88/92.',
             True),
            ('combo', 'Texture cache', texcache_var,
             _choices(TEX_CACHE_CHOICES),
             'Recycling of freed textures. Stock leaks and corrupts textures; '
             'Bounded small is the fix. FINDINGS-306.', True),
            ('combo', 'Background black level', bp_var,
             _choices(BLACK_POINT_CHOICES),
             'How much the background upscaling shader pulls down to pure '
             'black, in 255ths. Written into the shader copied to sdout; '
             'only the HD upscaling options use it. 11 is the tested value; '
             '12 is the most it can be.', False),
        ]),
        ('Developer — field backgrounds', [
            ('combo', 'Preset', fpre_var,
             _choices(FIELD_BG_PRESET_CHOICES),
             'Sets the controls below in one go. Changing any of them moves '
             'this to Custom.', True),
            ('combo', 'Page size', fbg_var,
             _choices(FIELD_BG_PAGE_PX_CHOICES),
             'Size and colour depth of the background pages. Cost per page '
             'is 6×px².', True),
            ('combo', 'Paletted (fx) page size', fd1_var,
             _choices(FIELD_BG_D1_PX_CHOICES),
             'Every 8-bit page shares one size, patched into exefs/main. '
             'FINDINGS-223/225.', True),
            ('combo', 'Truecolor pages per field', ftc_var,
             _choices(FIELD_BG_TRUECOLOR_CHOICES),
             'How many pages may be promoted to truecolor. Pair with '
             '“No growth”.', True),
            ('combo', 'Page growth', frepl_var,
             _choices(FIELD_BG_REPLACE_ONLY_CHOICES),
             'Whether promotion may add pages. No growth compacts to pay for '
             'them.', True),
            ('combo', 'Widescreen margin art', fmart_var,
             _choices(FIELD_BG_MARGIN_ART_CHOICES),
             'Writes the mod’s art into the 16:9 bands (and the 4:3 '
             'interior) as palette indices.', False),
            ('combo', 'Max pages per field', fmaxp_var,
             _choices(FIELD_BG_MAX_PAGES_CHOICES),
             'Too many pages and the loader stops: black squares. The log '
             'names every field over the limit.', True),
            ('combo', 'Memory budget', fbud_var,
             _choices(FIELD_BG_BUDGET_CHOICES),
             'Ceiling on truecolor texture memory per field, in runtime '
             'megabytes. Unlimited is the default; page counts are for '
             '768px pages.', True),
        ]),
        ('Developer — texture caps', [
            ('combo', 'Field models', cap_var,
             _choices(FIELD_TEX_CAP_CHOICES),
             'Downscales oversized char.lgp model textures.', False),
            ('combo', 'World map', world_cap_var,
             _choices(WORLD_TEX_CAP_CHOICES),
             'Downscales world_us.lgp textures and scales the world-mesh UVs '
             'to match.', False),
            ('combo', 'Battle backgrounds (Avalanche Arisen)', bg_cap_var,
             _choices(BATTLE_BG_TEX_CAP_CHOICES),
             'Arisen’s own tiles only. The rest of battle.lgp stays at '
             '256px.', False),
            ('combo', 'Spells (SYW Unified Spells)', spell_cap_var,
             _choices(SPELL_TEX_CAP_CHOICES),
             'Maximum upscale for mapped SYW spell, summon and effect '
             'textures.', False),
            ('combo', 'Minigames (SYW Unified Minigames)', mini_cap_var,
             _choices(MINIGAME_TEX_CAP_CHOICES),
             'Maximum upscale for the rebuilt minigame textures.', False),
            ('combo', 'Menu art (Enhanced Stock UI)', esuisc_var,
             _choices(ESUI_TEXSCALE_CHOICES),
             'Size the menu, font and battle-window sheets are rebuilt at '
             'from the mod\u2019s 4\u00d7 art. Bigger costs graphics memory '
             '(about 2.3\u00d7 at 3\u00d7, 4\u00d7 at 4\u00d7) and rebuilds '
             'menu_us.lgp.', False),
        ]),
        ('Developer — movies', [
            ('combo', 'Video size', mfit_var,
             _choices(MOVIE_FIT_CHOICES),
             '“Displayed area” resamples to the 960×672 box '
             'the console actually shows.', True),
            ('combo', 'Video colour', mcol_var,
             _choices(MOVIE_COLOUR_CHOICES),
             'The movie shader assumes BT.709. Choose BT.601 for packs made '
             'from the SD originals.', False),
        ]),
    ]

    def settings_sections():
        """The rows the dialog shows right now."""
        return USER_SECTIONS + (DEV_SECTIONS if nxsettings.dev_mode()
                                else [])

    # Every row, for code that sets vars from values (Restore defaults, the
    # Developer Settings toggle).
    SETTINGS_SECTIONS = USER_SECTIONS + DEV_SECTIONS

    _settings_win = [None]
    _settings_refill = [None]   # the open dialog's redraw, while it is open
    _theme_now = [current_theme()]

    def apply_globals(want):
        """
        Set the dialog's vars so snapshot_settings() saves `want` (a
        __global__ dict), then save once. Returns the keys it could not
        reach. The theme and the Developer Settings toggle are left alone.

        The vars hold display labels, and each one maps to its key in its
        own way, so rather than a second copy of every mapping this tries
        each row's choices and keeps the one whose snapshot matches `want`
        best. Saving is held off while it searches.
        """
        def score():
            g = snapshot_settings(raw=True)['__global__']
            return sum(1 for k, v in want.items() if g.get(k) == v)

        _ui_ready[0] = False
        try:
            if score() < len(want):
                # Twice: a few rows only take effect once another is set.
                for _pass in range(2):
                    for _section, rows in SETTINGS_SECTIONS:
                        for kind, _label, var, values, _b, _n in rows:
                            if var is theme_var or var is devset_var:
                                continue
                            choices = ((True, False) if kind == 'check'
                                       else tuple(values))
                            best, best_score = var.get(), -1
                            for c in choices:
                                var.set(c)
                                n = score()
                                if n > best_score:
                                    best, best_score = c, n
                            var.set(best)
        finally:
            _ui_ready[0] = True
        save_settings_now()
        g = snapshot_settings(raw=True)['__global__']
        return sorted(k for k, v in want.items() if g.get(k) != v)

    # BUILD 804: the Default/Modified note beside the Developer Settings
    # toggle, while the dialog is open. Refreshed on every save.
    _dev_note = [None]

    def refresh_dev_note():
        lbl = _dev_note[0]
        if lbl is None:
            return
        try:
            off = nxsettings.modified_keys(
                snapshot_settings(raw=True)['__global__'])
            if off:
                lbl.configure(text='Modified (%d)' % len(off),
                              foreground=WARN)
            else:
                lbl.configure(text='Default', foreground=GOOD)
        except tk.TclError:                        # dialog closed
            _dev_note[0] = None

    def _dev_changed():
        """
        Developer Settings was toggled. It only shows or hides the developer
        rows (BUILD 804): the build uses them as saved either way, so the
        dialog just redraws.
        """
        on = bool(devset_var.get())
        if nxsettings.env_dev_mode() and not on:
            devset_var.set(True)        # SEVENTH_NX_DEV wins; nothing to do
            return
        nxsettings.set_dev_override(on)
        save_settings_now()
        _show_dev_tag()
        try:
            n = len(nxsettings.modified_keys(
                snapshot_settings(raw=True)['__global__']))
        except Exception:                                   # noqa: BLE001
            n = 0
        log_write('Developer Settings %s. %s' % (
            'shown' if on else 'hidden',
            '%d differ from the tested defaults and are still used.' % n
            if n else 'All are at the tested defaults.'))
        if _settings_refill[0] is not None:
            _settings_refill[0]()

    devset_var.trace_add('write', lambda *_a: root.after_idle(_dev_changed)
                         if _ui_ready[0] else None)

    def on_theme_change():
        if current_theme() != _theme_now[0]:
            apply_theme(current_theme())

    def open_settings():
        win = tk.Toplevel(root)
        _settings_win[0] = win
        # Hidden until it is sized and placed: mapped straight away, Tk
        # shows it at the window manager's default spot and it visibly
        # jumps when the geometry below lands.
        win.withdraw()
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
        # BUILD 804: About Dev sits alone in the bottom-left corner, on the
        # same left edge as the settings above it.
        ttk.Button(footer, text='About Dev',
                   command=lambda: open_about(win)).pack(side='left')

        def restore_defaults():
            """Every setting, developer ones included, back to
            data/default_settings.json -- except the theme and the
            Developer Settings toggle."""
            want = default_globals()
            for k in ('gui_theme', 'developer_settings'):
                want.pop(k, None)
            if not want:
                messagebox.showerror('Restore defaults', 'data/default_'
                                     'settings.json is missing or empty.',
                                     parent=win)
                return
            if not messagebox.askyesno(
                    'Restore defaults',
                    'Put every setting back to its default?\n\n'
                    'This resets every setting in this window, developer '
                    'settings included, to the tested values in '
                    'data/default_settings.json. Your theme, the Developer '
                    'Settings toggle, your mods and their options are not '
                    'changed.\n\nAny setting you changed here will be lost.',
                    icon='warning', default='no', parent=win):
                return
            off = apply_globals(want)
            log_write('Settings restored to defaults.' + (
                ' Not offered here, left as they were: ' + ', '.join(off)
                if off else ''))

        ttk.Button(footer, text='Restore defaults',
                   command=restore_defaults).pack(side='right', padx=(0, 8))

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

        def fill():
            for child in body.winfo_children():
                child.destroy()
            dev = nxsettings.dev_mode()
            env = nxsettings.env_dev_mode()
            r = 0
            if env:
                ttk.Label(body, text='Developer mode is forced on by '
                          'SEVENTH_NX_DEV=1. The developer settings below '
                          'build exactly as set here.',
                          style='Sub.TLabel', wraplength=560,
                          justify='left'
                          ).grid(row=r, column=0, columnspan=2, sticky='w',
                                 pady=(0, 12))
                r += 1
            _fill_rows(r, dev, env)

        # BUILD 806. A settings checkbox drawn like the mod rows: the same
        # _Check box, and the label placed so the CAP BAND of its "A" is
        # centred on the box (see "optical vertical centring" further down),
        # not the label's line box, which ttk.Checkbutton centres and which
        # reads low. `text_y` is exposed so a note in the same row (Default /
        # Modified) sits on the same band.
        CHK_FONT = (UI_FONT, 11)        # the ttk default ('.') it replaces

        def _settings_check(parent, text, var, disabled=False):
            f = tkfont.Font(font=CHK_FONT)
            a, d = f.metrics('ascent'), f.metrics('descent')
            cap = _cap_height(CHK_FONT, a, d)
            h = (max(_CHK_SIZE, a + d) + 6) | 1    # odd -> a centre pixel
            mid = h // 2
            row = tk.Frame(parent, bg=BG_APP, height=h, bd=0,
                           highlightthickness=0)
            row.pack_propagate(False)
            row.grid_propagate(False)
            chk = _Check(row, var, BG_APP)
            chk.place(x=0, y=mid - (_CHK_SIZE - 1) // 2 + _CHK_NUDGE)
            row.text_y = mid - ((a - cap) + (cap - 1) // 2) + TEXT_RASTER_TWEAK
            lbl = tk.Label(row, text=text, bg=BG_APP,
                           fg=TEXT_MUTED if disabled else TEXT_PRIMARY,
                           font=CHK_FONT, bd=0, padx=0, pady=0,
                           highlightthickness=0)
            lbl.place(x=_CHK_SIZE + 6, y=row.text_y)
            if disabled:
                chk.unbind('<Button-1>')
                chk.configure(cursor='')
            else:
                lbl.configure(cursor='')
                lbl.bind('<Button-1>', chk._toggle)
            return row

        def _fill_rows(r, dev, env):
            for si, (section, rows) in enumerate(settings_sections()):
                ttk.Label(body, text=section.upper(), style='Sub.TLabel'
                          ).grid(row=r, column=0, columnspan=2,
                                 sticky='w', pady=(0 if si == 0 else 16, 6))
                r += 1
                for kind, label, var, values, blurb, needs in rows:
                    dim = needs and not have_nso
                    if kind == 'check':
                        w = _settings_check(body, label, var,
                                            disabled=var is devset_var and env)
                        w.grid(row=r, column=0, columnspan=2, sticky='we')
                    else:
                        ttk.Label(body, text=label).grid(
                            row=r, column=0, sticky='w', padx=(0, 12))
                        w = ttk.Combobox(
                            body, textvariable=var, state='readonly',
                            values=values if dev else getattr(values, 'user',
                                                              values),
                            width=44 if dev else 32)
                        w.grid(row=r, column=1, sticky='e')
                    if var is devset_var:
                        # in the toggle's own row, on its cap band
                        _dev_note[0] = tk.Label(
                            w, bg=BG_APP, fg=TEXT_SECONDARY, font=CHK_FONT,
                            bd=0, padx=0, pady=0, highlightthickness=0)
                        _dev_note[0].place(relx=1.0, y=w.text_y, anchor='ne')
                        refresh_dev_note()
                    r += 1
                    note = blurb + (' Needs a full game dump: saved now, '
                                    'used once dump/ has one.' if dim else '')
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

        def fit():
            """Size to the content, then cap the height so the window
            always fits on the screen it is opening on. Without the cap a
            long settings list simply grew off the bottom."""
            win.update_idletasks()
            # `body` is a ttk.Frame with padding=16, and a padded frame's
            # requested size ALREADY includes its padding -- adding it again
            # here is how a dialog ends up with 32px of dead space.
            want_w = body.winfo_reqwidth()
            want_h = body.winfo_reqheight() + footer.winfo_reqheight()
            max_h = int(win.winfo_screenheight() * 0.86)
            height = min(want_h, max_h)
            if height < want_h:
                # The scrollbar is about to appear inside the canvas width,
                # so widen by its thickness rather than letting it eat 16px
                # of the blurbs.
                want_w += 18
            win.minsize(1, 1)
            win.maxsize(want_w, want_h)
            win.geometry('%dx%d' % (want_w, height))
            win.minsize(want_w, min(420, height))
            return want_w, height

        fill()
        want_w, height = fit()

        def refill():
            if not win.winfo_exists():
                return
            top = canvas.yview()[0]
            fill()
            w, _h = fit()
            canvas.yview_moveto(top)
            # keep it centred on the main window now that its width changed
            x = root.winfo_rootx() + (root.winfo_width() - w) // 2
            win.geometry('+%d+%d' % (max(x, 0), win.winfo_y()))
        _settings_refill[0] = refill

        def _forget_refill(evt):
            if evt.widget is win:
                _settings_refill[0] = None
                _dev_note[0] = None
        win.bind('<Destroy>', _forget_refill, add='+')

        # centre on the main window, then pull back on-screen if the cap
        # still leaves it hanging off the bottom
        win.update_idletasks()
        x = root.winfo_rootx() + (root.winfo_width() - want_w) // 2
        y = root.winfo_rooty() + 60
        if y + height > win.winfo_screenheight() - 40:
            y = max(20, win.winfo_screenheight() - height - 40)
        win.geometry('+%d+%d' % (max(x, 0), max(y, 0)))
        win.deiconify()
        try:
            win.wait_visibility()       # a grab needs a viewable window
        except tk.TclError:                            # pragma: no cover
            pass
        win.grab_set()
        win.focus_set()

    # Defined here, ahead of the header, because the header buttons below
    # are the first thing that centres text on it; the mod rows reuse it.
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

    # ---------- Install Mods: download / update the supported mods -------
    # The mods in data/mod_sources.json, from 7th Heaven's own catalogs (see
    # ff7nx_download). One row per mod: what is installed, what the catalog
    # has, and an Install or Update button; Install all does every row that
    # needs it, and turns into Cancel while it runs. Work runs on a thread, one mod
    # at a time, and reports through a queue this window drains with
    # after() -- Tk must only be touched from the main thread.
    #
    # Each mod that lands here goes straight into the main window's list
    # (reload_mods), no restart. settings.json keys mods by file name and every download lands
    # on the name it lists, so their on/off state and options carry over.
    _getmods = {'win': None}

    def restore_mod_defaults(parent):
        """Every supported mod's on/off state and options back to
        data/default_mod_settings.json. A mod it does not list is left
        alone, and so is everything outside the mod list."""
        want = default_mod_settings()
        if not want:
            messagebox.showerror('Restore defaults', 'data/default_mod_'
                                 'settings.json is missing or empty.',
                                 parent=parent)
            return
        if build_btn.instate(['disabled']):
            messagebox.showinfo(
                'Restore defaults', 'A build is running. Restore the mod '
                'defaults once it has finished.', parent=parent)
            return
        if not messagebox.askyesno(
                'Restore defaults',
                'Put every supported mod back to its tested settings?\n\n'
                'This turns each of the %d supported mods on and resets all '
                'of their options to the configuration 7th Heaven NX is '
                'tested with. Use it if a build stopped working after you '
                'changed mod options.\n\nYour app settings, theme and '
                'downloaded mods are not changed. Any mod option you '
                'changed will be lost.' % len(want),
                icon='warning', default='no', parent=parent):
            return
        done = 0
        for mod in mods:
            entry = want.get(mod.filename)
            if entry is None:
                continue
            settings_by_mod[mod.filename] = _mod_options_from_saved(
                mod, {mod.filename: entry})
            var = enabled.get(mod.filename)
            if var is not None:
                var.set(entry['enabled'])
            done += 1
        current = next((m for m in mods if m.filename == selected.get()),
                       None)
        if current is not None:
            show_options(current)
        save_settings_now()
        _sync_dflt()
        later = len(want) - done
        log_write('Mod settings restored to defaults for %d mod%s.%s' % (
            done, '' if done == 1 else 's',
            ' %d not installed yet; they start with their defaults when '
            'installed.' % later if later else ''))

    def open_get_mods():
        import ff7nx_download as dl
        if _getmods['win'] is not None and _getmods['win'].winfo_exists():
            _getmods['win'].lift()
            _getmods['win'].focus_set()
            return
        try:
            sources = dl.load_sources()
        except (OSError, ValueError) as exc:
            messagebox.showerror('Install Mods', 'data/mod_sources.json could '
                                 'not be read:\n\n%s' % exc)
            return
        dl.cleanup(MODS_DIR)

        win = tk.Toplevel(root)
        _getmods['win'] = win
        # Hidden until it is sized and placed: mapped straight away, Tk
        # shows it at the window manager's default spot and it visibly
        # jumps when the geometry below lands.
        win.withdraw()
        win.title('Install Mods')
        win.configure(bg=BG_APP)
        win.transient(root)
        win.minsize(860, 420)              # BUILD 814: room for Open mods folder

        top = ttk.Frame(win, padding=(16, 14, 16, 6))
        top.pack(side='top', fill='x')
        ttk.Label(top, text='Supported mods', font=(UI_FONT, 13, 'bold')
                  ).pack(side='left')
        catalog_lbl = ttk.Label(top, text='', style='Sub.TLabel')
        catalog_lbl.pack(side='left', padx=12)
        check_btn = ttk.Button(top, text='Check for updates')
        check_btn.pack(side='right')
        ttk.Button(top, text='Restore defaults',
                   command=lambda: restore_mod_defaults(win)
                   ).pack(side='right', padx=(0, 8))
        # BUILD 814: left of Restore defaults (packed right to left)
        def open_mods_folder():
            os.makedirs(MODS_DIR, exist_ok=True)
            _reveal(MODS_DIR)
        ttk.Button(top, text='Open mods folder', command=open_mods_folder
                   ).pack(side='right', padx=(0, 8))

        # Right to left: Close, Install all (Cancel while running). The
        # status text sits
        # left of the buttons and the bar takes whatever width is left, so
        # there is no dead gap between them.
        footer = ttk.Frame(win, padding=(16, 10, 16, 14))
        footer.pack(side='bottom', fill='x')
        close_btn = ttk.Button(footer, text='Close')
        close_btn.pack(side='right')
        all_btn = ttk.Button(footer, text='Install all', width=16)
        all_btn.pack(side='right', padx=(0, 8))
        gm_status = ttk.Label(footer, text='', style='Sub.TLabel')
        gm_status.pack(side='right', padx=(10, 12))
        gm_bar = ttk.Progressbar(footer, mode='determinate')
        gm_bar.pack(side='left', fill='x', expand=True)

        area = ttk.Frame(win, padding=(16, 0))
        area.pack(side='top', fill='both', expand=True)
        _c, table = make_scrollable(area, inner_width_tracks_canvas=True)
        # BUILD 814: column 0 is each mod's check box -- Install all only
        # does the checked rows; the per-row Install / Update link still
        # works for any row.
        table.columnconfigure(1, weight=1)
        for col, (text, w) in enumerate((('', 24), ('MOD', 0),
                                         ('INSTALLED', 90),
                                         ('LATEST', 90), ('SIZE', 80),
                                         ('STATUS', 150))):
            table.columnconfigure(col, minsize=w)
            ttk.Label(table, text=text, style='ColHeader.TLabel').grid(
                row=0, column=col, sticky='w', padx=(0, 10 if col else 4),
                pady=(4, 2))
        ttk.Separator(table, orient='horizontal').grid(
            row=1, column=0, columnspan=6, sticky='ew', pady=(0, 4))

        state = {'catalog': {}, 'busy': False, 'cancel': False,
                 'installed': 0, 'jobs': []}
        msgs = queue.Queue()
        rows = {}

        versions = {}       # path -> installed version, until it changes

        def installed_version(src):
            path = os.path.join(MODS_DIR, src['file'])
            if not os.path.exists(path):
                return None
            if path not in versions:
                # the startup list's manifest, unless this window replaced
                # the file since (then the list still holds the old one)
                m = next((x for x in mods if x.path == path), None)
                if m is None or m.manifest is None or path in fresh:
                    m = build.Mod(path, CACHE_DIR)
                    m._load_manifest()
                versions[path] = (m.manifest.version
                                  if m.manifest else '') or '?'
            return versions[path]

        fresh = set()       # paths this window installed

        def size_text(n):
            if not n:
                return ''
            return ('%.1f GB' % (n / 2 ** 30) if n >= 2 ** 30
                    else '%d MB' % max(1, round(n / 2 ** 20)))

        def need(src):
            """'download', 'update' or None for this row."""
            cat = state['catalog'].get(src['id'])
            have = installed_version(src)
            if have is None:
                return 'download' if cat else None
            if cat and have != '?' and dl.is_newer(cat.version, have):
                return 'update'
            return None

        sel_vars = {}

        def selected_src(src):
            return install_sel.get(src['id'], True)

        def on_select(src):
            v = bool(sel_vars[src['id']].get())
            rows[src['id']]['name'].configure(
                style='TLabel' if v else 'GetModOff.TLabel')
            if v:
                install_sel.pop(src['id'], None)    # the default: not stored
            else:
                install_sel[src['id']] = False
            save_settings_now()
            refresh()

        for i, src in enumerate(sources):
            r = i + 2
            var = tk.BooleanVar(value=selected_src(src))
            sel_vars[src['id']] = var
            # takefocus=False: a clicked box would otherwise keep Tk's
            # dotted focus ring around itself (BUILD 814b)
            # the main list's own rounded box with a tick (ttk's clam
            # Checkbutton draws an X)
            _Check(table, var, BG_APP,
                   command=lambda s=src: on_select(s)).grid(
                row=r, column=0, sticky='w', padx=(0, 6), pady=3)
            # an unchecked mod's name is dimmed (BUILD 814b)
            name = ttk.Label(table, text=src['name'],
                             style='TLabel' if selected_src(src)
                             else 'GetModOff.TLabel')
            name.grid(row=r, column=1, sticky='w', padx=(0, 10), pady=3)
            have = ttk.Label(table, style='Sub.TLabel')
            have.grid(row=r, column=2, sticky='w', padx=(0, 10))
            latest = ttk.Label(table, style='Sub.TLabel')
            latest.grid(row=r, column=3, sticky='w', padx=(0, 10))
            size = ttk.Label(table, style='Sub.TLabel')
            size.grid(row=r, column=4, sticky='w', padx=(0, 10))
            cell = ttk.Frame(table)
            cell.grid(row=r, column=5, sticky='w')
            # Install / Update is a link, not a button, so the column reads
            # as text like "Up to date" beside it.
            btn = ttk.Label(cell, style='Link.TLabel', cursor='')
            btn.bind('<Button-1>', lambda e, b=btn: b.command())
            note = ttk.Label(cell, style='Sub.TLabel')
            rows[src['id']] = dict(name=name, have=have, latest=latest,
                                   size=size, btn=btn, note=note)

        def show_row(src, note=None):
            w = rows[src['id']]
            cat = state['catalog'].get(src['id'])
            have = installed_version(src)
            if cat:
                w['name'].configure(text=cat.name)
            w['have'].configure(text=have or '—')
            w['latest'].configure(text=cat.version if cat else '—')
            w['size'].configure(text=size_text(cat.size) if cat else '')
            what = need(src)
            w['btn'].pack_forget()
            w['note'].pack_forget()
            if note is None and what and not state['busy']:
                w['btn'].configure(
                    text='Install' if what == 'download' else 'Update')
                w['btn'].command = lambda s=src: start([s])
                w['btn'].pack(side='left')
                return
            if note is None:
                # "Up to date" only when the catalog said so: with no
                # catalog loaded there is nothing to compare against.
                note = ('Queued' if src in state['jobs'] else
                        'Not in the catalog' if state['catalog'] and not cat
                        else 'Up to date' if have and cat else
                        'Installed' if have else '')
            w['note'].configure(text=note)
            w['note'].pack(side='left')

        def refresh():
            for src in sources:
                show_row(src)
            # BUILD 814: only the checked rows
            todo = [s for s in sources if need(s) and selected_src(s)]
            idle = not state['busy']
            check_btn.state(['!disabled' if idle else 'disabled'])
            if state['jobs']:
                # installing: the same button cancels
                all_btn.configure(text='Cancel', command=cancel)
                all_btn.state(['disabled' if state['cancel']
                               else '!disabled'])
                return
            all_btn.configure(
                text='Install all (%d)' % len(todo) if todo
                else 'Install all',
                command=lambda: start([s for s in sources
                                       if need(s) and selected_src(s)]))
            all_btn.state(['!disabled' if idle and todo else 'disabled'])

        def worker_catalog():
            try:
                cat = dl.load_catalogs(
                    log=lambda s: msgs.put(('log', s)))
                msgs.put(('catalog', cat))
            except Exception as exc:                           # noqa: BLE001
                msgs.put(('catalog_error', str(exc)))

        def check():
            state['busy'] = True
            catalog_lbl.configure(text='Checking the 7th Heaven catalogs…')
            gm_bar.configure(mode='indeterminate')
            gm_bar.start(15)
            refresh()
            threading.Thread(target=worker_catalog, daemon=True).start()

        def worker_install(jobs):
            # BUILD 832: dl.PARALLEL_MODS downloads at once -- the mirrors
            # cap each connection, so the small mods finish while the big
            # ones are still coming -- each with its own retries
            pending = list(enumerate(jobs))
            plock = threading.Lock()

            def one():
                while True:
                    with plock:
                        if state['cancel'] or not pending:
                            return
                        n, src = pending.pop(0)
                    cat = state['catalog'][src['id']]
                    msgs.put(('job', (n, len(jobs), src)))

                    def prog(done, total, src=src):
                        msgs.put(('bytes', (src, done, total)))
                    try:
                        path = dl.download_and_install(
                            cat, MODS_DIR, src['file'], prog,
                            cancel=lambda: state['cancel'],
                            log=lambda s, src=src: msgs.put(
                                ('log', '%s: %s' % (src['name'], s))),
                            status=lambda t, src=src: msgs.put(
                                ('status', (src, t))))
                        msgs.put(('installed', (src, path)))
                    except dl.Cancelled:
                        msgs.put(('failed', (src, 'cancelled')))
                        return
                    except Exception as exc:                   # noqa: BLE001
                        msgs.put(('failed', (src, str(exc))))

            workers = [threading.Thread(target=one, daemon=True)
                       for _ in range(max(1, min(dl.PARALLEL_MODS,
                                                 len(jobs))))]
            for t in workers:
                t.start()
            for t in workers:
                t.join()
            msgs.put(('finished', None))

        def start(jobs):
            if state['busy'] or not jobs:
                return
            state.update(busy=True, cancel=False, jobs=list(jobs),
                         bytes={}, started=0, ended=0, total=len(jobs),
                         sizes=sum(getattr(state['catalog'].get(j['id']),
                                           'size', 0) or 0 for j in jobs))
            gm_bar.stop()
            gm_bar.configure(mode='determinate', maximum=1, value=0)
            refresh()
            threading.Thread(target=worker_install, args=(list(jobs),),
                             daemon=True).start()

        def cancel():
            state['cancel'] = True
            all_btn.state(['disabled'])
            gm_status.configure(text='Cancelling…')

        def _gm_count():
            # several run at once, so say how many are done and running
            # rather than which one is "current"
            done = state.get('ended', 0)
            running = state.get('started', 0) - done
            gm_status.configure(text='%d of %d done · %d downloading' % (
                done, state.get('total', 0), running))

        def pump_getmods():
            if not win.winfo_exists():
                return
            try:
                while True:
                    kind, p = msgs.get_nowait()
                    if kind == 'log':
                        log_write('Install Mods: ' + p)
                    elif kind == 'catalog':
                        state['catalog'] = p
                        state['busy'] = False
                        gm_bar.stop()
                        gm_bar.configure(mode='determinate', value=0)
                        found = sum(1 for s in sources if s['id'] in p)
                        catalog_lbl.configure(
                            text='%d of %d found in the catalogs'
                            % (found, len(sources)) if p else
                            'Catalogs unavailable -- check your connection')
                        refresh()
                    elif kind == 'catalog_error':
                        state['busy'] = False
                        gm_bar.stop()
                        catalog_lbl.configure(text='Catalog check failed: '
                                              + p)
                        refresh()
                    elif kind == 'job':
                        n, total, src = p
                        state['started'] = state.get('started', 0) + 1
                        state['total'] = total
                        _gm_count()
                        show_row(src, 'Starting…')
                    elif kind == 'status':
                        src, text = p
                        show_row(src, text)
                    elif kind == 'bytes':
                        src, done, total = p
                        # one bar for the whole run, over every mod in it
                        got = state.setdefault('bytes', {})
                        got[src['id']] = done
                        allb = sum(got.values())
                        gm_bar.configure(
                            maximum=max(state.get('sizes') or 0, allb, 1),
                            value=allb)
                        if total:
                            text = 'Downloading %d%%' % (100 * done // total)
                        else:
                            text = 'Downloading %s' % size_text(done)
                        if done and total and done >= total:
                            text = 'Installing…'
                        show_row(src, text)
                    elif kind == 'installed':
                        src, path = p
                        state['installed'] += 1
                        fresh.add(os.path.join(MODS_DIR, src['file']))
                        for k in [k for k in versions
                                  if k.startswith(os.path.join(MODS_DIR, ''))
                                  and os.path.basename(k) in (
                                      src['file'], os.path.basename(path))]:
                            del versions[k]
                        if src in state['jobs']:
                            state['jobs'].remove(src)
                        state['ended'] = state.get('ended', 0) + 1
                        _gm_count()
                        log_write('Install Mods: installed %s -> %s'
                                  % (src['name'], os.path.relpath(path, HERE)))
                        show_row(src, 'Installed')
                        # straight into the main list, no restart
                        reload_mods()
                    elif kind == 'failed':
                        src, why = p
                        if src in state['jobs']:
                            state['jobs'].remove(src)
                        state['ended'] = state.get('ended', 0) + 1
                        _gm_count()
                        log_write('Install Mods: %s failed -- %s'
                                  % (src['name'], why))
                        show_row(src, 'Cancelled' if why == 'cancelled'
                                 else 'Failed (see log)')
                    elif kind == 'finished':
                        state.update(busy=False, jobs=[], cancel=False)
                        gm_bar.configure(value=0)
                        n = state['installed']
                        if n and build_btn.instate(['disabled']):
                            text = ('%d mod(s) installed, listed once the '
                                    'build ends' % n)
                        elif n:
                            text = '%d mod(s) installed and listed' % n
                        else:
                            text = ''
                        gm_status.configure(text=text)
                        refresh()
            except queue.Empty:
                pass
            win.after(100, pump_getmods)

        def close():
            if state['busy'] and state['jobs']:
                if not messagebox.askyesno(
                        'Install Mods', 'A download is still running. Cancel '
                        'it and close?', parent=win):
                    return
                state['cancel'] = True
            win.destroy()

        check_btn.configure(command=check)
        close_btn.configure(command=close)
        win.protocol('WM_DELETE_WINDOW', close)

        win.update_idletasks()
        w, h = 920, 620                    # BUILD 814: was 820 (one more header button)
        x = root.winfo_rootx() + (root.winfo_width() - w) // 2
        y = root.winfo_rooty() + 40
        win.geometry('%dx%d+%d+%d' % (w, h, max(x, 0), max(y, 0)))
        win.deiconify()
        refresh()
        check()
        pump_getmods()

    # ---------- header buttons: Settings, Support (Ko-fi), Install Mods ----------------
    # Both are drawn the same way: a 16px icon from assets/, a gap, the
    # label. gear.png and kofi.png are drawn in TEXT_PRIMARY (the stock Ko-fi
    # cup's black outline vanishes on this dark header).
    #
    # Drawn on a Canvas, not as ttk.Buttons, for the same reason the mod rows
    # are: a ttk.Button centres the label's LINE BOX, so descent space sits
    # under the capitals and the word reads low. Here the cap band of "A" in
    # the button font is centred on the button's centre pixel, as for the
    # rows (see "optical vertical centring" below), and the icon is centred
    # on the same pixel. Without an icon, or without Pillow, a button is its
    # label alone.
    BTN_FONT = (UI_FONT, 11)        # the ttk default ('.') it replaces
    BTN_ICON = 16
    BTN_PAD_X, BTN_GAP = 10, 6
    BTN_H = (BTN_ICON + 2 * 7) | 1      # odd -> a centre pixel exists
    _btn_mid = BTN_H // 2
    _btnf = tkfont.Font(font=BTN_FONT)
    _btn_a, _btn_d = _btnf.metrics('ascent'), _btnf.metrics('descent')
    _btn_cap = _cap_height(BTN_FONT, _btn_a, _btn_d)
    # -1 is TEXT_RASTER_TWEAK, the calibration measured for the mod rows
    # (Tk's glyph rasterisation lands the ink a pixel low on this platform).
    _btn_text_y = _btn_mid - ((_btn_a - _btn_cap) + (_btn_cap - 1) // 2) - 1

    _tinted_icons = []      # (PhotoImage, name): repainted by apply_theme

    def _button_icon(name):
        try:
            from PIL import ImageTk as _KImgTk
            img = _KImgTk.PhotoImage(_button_icon_art(name))
        except Exception:
            return None
        _icon_keep.append(img)          # Tk's weak image reference
        _tinted_icons.append((img, name))
        return img

    def _button_icon_art(name):
        # aliased: a bare `Image` here would become a local of the whole
        # GUI function and shadow the name for its nested helpers.
        from PIL import Image as _KImg
        im = _KImg.open(os.path.join(nxpaths.APP, 'assets', name)
                        ).convert('RGBA')
        # The icons are drawn in Abyss Blue's text colour; repaint those
        # pixels (alpha kept) in this theme's. Ko-fi's orange and white
        # are left alone.
        src = (0xe8, 0xec, 0xf4)
        dst = tuple(int(TEXT_PRIMARY[i:i + 2], 16) for i in (1, 3, 5))
        if dst != src:
            px = im.load()
            for y in range(im.size[1]):
                for x in range(im.size[0]):
                    r, g, b, a = px[x, y]
                    if a and all(abs(c - s0) <= 12 for c, s0
                                 in zip((r, g, b), src)):
                        px[x, y] = dst + (a,)
        return im.resize((BTN_ICON, BTN_ICON),
                         getattr(_KImg, 'LANCZOS', _KImg.BICUBIC))

    def header_button(text, icon, command):
        icon = _button_icon(icon)
        x = BTN_PAD_X
        w = x + (BTN_ICON + BTN_GAP if icon else 0) + \
            _btnf.measure(text) + BTN_PAD_X
        c = tk.Canvas(header, width=w, height=BTN_H, bg=BG_APP, bd=0,
                      highlightthickness=0)
        # What clam drew for TButton: a 1px BORDER edge, a 1px ring in
        # BG_APP (its light/dark colours), then the fill.
        c.create_rectangle(0, 0, w - 1, BTN_H - 1, outline=BORDER)
        # (an outline-less rectangle fills x1..x2-1, hence w - 2)
        fill = c.create_rectangle(2, 2, w - 2, BTN_H - 2, outline='',
                                  fill=BG_ROW)
        if icon:
            # a run of n pixels centres on top + (n-1)//2
            c.create_image(x, _btn_mid - (BTN_ICON - 1) // 2, image=icon,
                           anchor='nw')
            x += BTN_ICON + BTN_GAP
        c.create_text(x, _btn_text_y, text=text, font=BTN_FONT,
                      fill=TEXT_PRIMARY, anchor='nw')
        pressed = [False]

        def paint(bg):
            c.itemconfigure(fill, fill=bg)

        def release(e):
            inside = 0 <= e.x < w and 0 <= e.y < BTN_H
            paint(BG_ROW_HOVER if inside else BG_ROW)
            if pressed[0] and inside:
                command()
            pressed[0] = False

        def press(_e):
            pressed[0] = True
            paint(BG_ROW_SEL)

        c.bind('<Enter>', lambda e: paint(BG_ROW_SEL if pressed[0]
                                          else BG_ROW_HOVER))
        c.bind('<Leave>', lambda e: paint(BG_ROW))
        c.bind('<ButtonPress-1>', press)
        c.bind('<ButtonRelease-1>', release)
        return c

    KOFI_URL = 'https://ko-fi.com/ppkantorski'

    def open_support():
        import webbrowser
        webbrowser.open(KOFI_URL)

    # ---------- Settings > About Dev (BUILD 804) ----------
    # The avatar ships as assets/dev_avatar.png, already cut to a circle, and
    # the bio is the GitHub profile's, copied here. Nothing is fetched: the
    # page has to open offline and must not stall the GUI on a network call.
    DEV_NAME = 'ppkantorski'
    DEV_URL = 'https://github.com/ppkantorski'
    DEV_BIO = ('Software developer with background in embedded systems, '
               'signal processing, machine learning / AI, electrical '
               'engineering, and astrophysics research.')
    DEV_CREDITS = ('Special thanks to FFNx, 7th Heaven, Tsunamods, Ad0ky, '
                   'and many others. \u2665\ufe0f')
    DEV_AVATAR = os.path.join(nxpaths.APP, 'assets', 'dev_avatar.png')
    DEV_AVATAR_PX = 112
    _about_win = [None]

    def open_about(parent=None):
        import webbrowser
        if _about_win[0] is not None and _about_win[0].winfo_exists():
            _about_win[0].lift()
            _about_win[0].focus_set()
            return
        owner = parent or root
        win = tk.Toplevel(owner)
        _about_win[0] = win
        # Hidden until it is sized and placed: mapped straight away, Tk
        # shows it at the window manager's default spot and it visibly
        # jumps when the geometry below lands.
        win.withdraw()
        win.title('About the Developer')
        win.configure(bg=BG_APP)
        win.transient(owner)
        win.resizable(False, False)

        # A card: avatar, name and role on the left; a divider; the bio and
        # the profile link on the right. tk widgets rather than ttk inside
        # the card, so they can take its BG_ROW fill (apply_theme recolours
        # them like every other tk widget).
        body = ttk.Frame(win, padding=(20, 20, 20, 14))
        body.pack(fill='both', expand=True)
        card = tk.Frame(body, bg=BG_ROW, highlightthickness=1,
                        highlightbackground=BORDER, highlightcolor=BORDER)
        card.pack(fill='both', expand=True)

        left = tk.Frame(card, bg=BG_ROW)
        left.grid(row=0, column=0, padx=(28, 24), pady=24)
        # The avatar inside a 2px accent ring, on a Canvas so the ring is
        # drawn in this theme's colour rather than baked into the PNG.
        ring, gap = 2, 3
        side = DEV_AVATAR_PX + 2 * (ring + gap)
        c = tk.Canvas(left, width=side, height=side, bg=BG_ROW, bd=0,
                      highlightthickness=0)
        c.pack()
        c.create_oval(1, 1, side - 2, side - 2, outline=ACCENT, width=ring)
        av = _load_icon(DEV_AVATAR_PX, DEV_AVATAR)
        if av is not None:
            c.create_image(side // 2, side // 2, image=av)
        else:
            c.create_text(side // 2, side // 2, text=DEV_NAME[:1].upper(),
                          font=(UI_FONT, 40, 'bold'), fill=TEXT_PRIMARY)
        tk.Label(left, text=DEV_NAME, bg=BG_ROW, fg=TEXT_PRIMARY,
                 font=(UI_FONT, 17, 'bold')).pack(pady=(12, 0))
        tk.Label(left, text='7HNX Developer', bg=BG_ROW, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 11)).pack(pady=(2, 0))

        tk.Frame(card, bg=BORDER, width=1).grid(row=0, column=1, sticky='ns',
                                                 pady=24)

        right = tk.Frame(card, bg=BG_ROW)
        right.grid(row=0, column=2, sticky='w', padx=(24, 28), pady=24)

        def heading(text, top, parent=None, **grid):
            lbl = tk.Label(parent or right, text=text, bg=BG_ROW,
                           fg=TEXT_MUTED, font=(UI_FONT, 9, 'bold'))
            if grid:
                lbl.grid(sticky='w', pady=(top, 4), **grid)
            else:
                lbl.pack(anchor='w', pady=(top, 4))

        def url_label(parent, url, **grid):
            lbl = tk.Label(parent, text=url.split('://', 1)[1], bg=BG_ROW,
                           fg=ACCENT_SOFT, cursor='',
                           font=(UI_FONT, 12, 'underline'))
            lbl.grid(sticky='w', **grid)
            lbl.bind('<Button-1>', lambda _e: webbrowser.open(url))

        heading('ABOUT', 0)
        tk.Label(right, text=DEV_BIO, bg=BG_ROW, fg=TEXT_PRIMARY,
                 font=(UI_FONT, 12), wraplength=300, justify='left'
                 ).pack(anchor='w')
        # GitHub and Ko-fi side by side, each heading over its link.
        links = tk.Frame(right, bg=BG_ROW)
        links.pack(anchor='w')
        heading('GITHUB', 16, links, row=0, column=0)
        url_label(links, DEV_URL, row=1, column=0, padx=(0, 28))
        heading('KO-FI', 16, links, row=0, column=1)
        url_label(links, KOFI_URL, row=1, column=1)
        heading('CREDITS', 16)
        tk.Label(right, text=DEV_CREDITS, bg=BG_ROW, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 11), wraplength=300, justify='left'
                 ).pack(anchor='w')

        foot = ttk.Frame(win, padding=(20, 0, 20, 20))
        foot.pack(fill='x')
        ttk.Label(foot, text='© 2026 %s' % DEV_NAME, style='Sub.TLabel'
                  ).pack(side='left')
        ttk.Button(foot, text='Close', command=win.destroy
                   ).pack(side='right')
        ttk.Button(foot, text='View on GitHub', style='Accent.TButton',
                   command=lambda: webbrowser.open(DEV_URL)
                   ).pack(side='right', padx=(0, 8))
        win.bind('<Escape>', lambda _e: win.destroy())

        # centre on whatever opened it
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        x = owner.winfo_rootx() + (owner.winfo_width() - w) // 2
        y = owner.winfo_rooty() + (owner.winfo_height() - h) // 2
        win.geometry('+%d+%d' % (max(x, 0), max(y, 0)))
        win.deiconify()
        # Settings holds the input grab (it is modal), so a second window
        # gets no clicks until the grab moves to it. Take it while open and
        # hand it back to whichever window had it when this one closes.
        prev = win.grab_current()

        def _give_back(evt):
            if evt.widget is win and prev is not None:
                try:
                    if prev.winfo_exists():
                        prev.grab_set()
                except tk.TclError:                    # pragma: no cover
                    pass
        win.bind('<Destroy>', _give_back, add='+')
        try:
            win.wait_visibility()
            win.grab_set()
        except tk.TclError:                            # pragma: no cover
            pass
        win.focus_set()

    # Packed side='right' in this order: Install Mods, Support, Settings.
    header_button('Settings', 'gear.png', open_settings).pack(side='right')
    header_button('Support', 'kofi.png', open_support
                  ).pack(side='right', padx=(0, 8))
    header_button('Install Mods', 'download.png', open_get_mods
                  ).pack(side='right', padx=(0, 8))

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

    def snapshot_settings(raw=False):
        persist = {}
        for mod in mods:
            var = enabled.get(mod.filename)
            persist[mod.filename] = {
                'enabled': (bool(var.get()) if var is not None
                            else NEW_MOD_ENABLED),
                'options': settings_by_mod.get(mod.filename, {}),
            }
        persist['__global__'] = {'mod_load_order': [m.filename for m in mods],
                                 MOVED_KEY: [m.filename for m in mods
                                             if m.filename in moved],
                                 'field_tex_cap': current_field_tex_cap(),
                                 'world_tex_cap': current_world_tex_cap(),
                                 'battle_bg_tex_cap': current_battle_bg_tex_cap(),
                                 'spell_tex_cap': current_spell_tex_cap(),
                                 'minigame_tex_cap':
                                     current_minigame_tex_cap(),
                                 'spell_fx_cap': current_spell_fx_cap(),
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
                                         'field_bg_clamp_palettes', False)),
                                 'field_bg_preserve_cell_coords':
                                     bool(_global_setting(
                                         'field_bg_preserve_cell_coords',
                                         True)),
                                 # FINDINGS-228. Carried through here for the
                                 # reason the comment below gives: a key this
                                 # dict does not know about is dropped from
                                 # __global__ on the next Build.
                                 'field_bg_multipal':
                                     bool(_global_setting(
                                         'field_bg_multipal', True)),
                                 # FINDINGS-232. Comma-separated field names,
                                 # empty by default. An fx BASE on a truecolor
                                 # page is a shape the stock game never ships
                                 # (0 of 104,925 pairs) and it crashes
                                 # `wcrimb_2`, so it is opt-in PER FIELD and
                                 # never global.
                                 'field_bg_fx_base_fields':
                                     str(_global_setting(
                                         'field_bg_fx_base_fields', '') or ''),
                                 # FINDINGS-223. Same reason as the block
                                 # above, and it caught me: the key was
                                 # written into settings.json by hand, the
                                 # user pressed Build, and this dict -- which
                                 # does not know the key exists -- rebuilt
                                 # __global__ without it. The setting had
                                 # already been read into the environment by
                                 # then, so the build ran at 512 and the file
                                 # came back saying 256.
                                 # NOW A DROPDOWN, so it comes from the
                                 # widget like every other control rather
                                 # than being echoed back from the file. The
                                 # FINDINGS-223 note above is kept because
                                 # the hazard it describes is still real for
                                 # anyone hand-editing settings.json with the
                                 # GUI open -- the dict is still rebuilt from
                                 # scratch on save, and the widget still wins.
                                 'field_bg_d1_px':
                                     int(current_field_bg_d1_px()),
                                 # HANDOFF-224 s0.11, and the rule is now
                                 # older than the build that paid for it:
                                 # ANY setting a build adds goes in THIS
                                 # block, read-only or not. Build 109's
                                 # depth-1 ART switch -- on means the lift
                                 # writes Cosmos's 512px art where it has it,
                                 # off means it replicates and the build is
                                 # build 108 exactly. It is the A/B for
                                 # "is the art the thing I am looking at".
                                 'field_bg_d1_art':
                                     bool(_global_setting(
                                         'field_bg_d1_art', True)),
                                 'fps_60': bool(fps_var.get()),
                                 'movie_quality': current_movie_quality(),
                                 'movie_fit': current_movie_fit(),
                                 'movie_colour': current_movie_colour(),
                                 'hd_black_point': current_black_point(),
                                 'movie_30fps': bool(m30_var.get()),
                                 'analog_360': bool(a360_var.get()),
                                 'no_autorun': bool(norun_var.get()),
                                 'texture_cache': current_texture_cache(),
                                 'field_frame': bool(frame_var.get()),
                                 'model_shadows': bool(shadows_var.get()),
                                 'no_cheats': bool(nocheat_var.get()),
                                 'witem_fix': bool(witem_var.get()),
                                 'advanced_facial': bool(facial_var.get()),
                                 'text_autosize': bool(autosize_var.get()),
                                 'compact_cache': bool(compact_var.get()),
                                 'limiter_fps': current_limiter_fps(),
                                 'widescreen': current_widescreen(),
                                 'field_buffer': current_field_buffer(),
                                 'gfx_pool_mb': current_gfx_pool(),
                                 'scaler': current_scaler(),
                                 'fxaa': current_fxaa(),
                                 'video_shader': current_video_shader(),
                                 'voice_normalize':
                                     current_voice_normalize(),
                                 'esui_texscale': current_esui_texscale(),
                                 'dock_resolution':
                                     current_dock_resolution(),
                                 'title_screen': current_title_screen(),
                                 'gui_theme': current_theme(),
                                 'developer_settings':
                                     bool(devset_var.get()),
                                 # BUILD 814: Install Mods' check boxes
                                 'install_selection': dict(install_sel)}
        # BUILD 804: saved as the vars hold them, developer mode or not.
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
        refresh_dev_note()

    fps_var.trace_add('write', save_settings_now)
    ws_var.trace_add('write', save_settings_now)
    cap_var.trace_add('write', save_settings_now)
    world_cap_var.trace_add('write', save_settings_now)
    bg_cap_var.trace_add('write', save_settings_now)
    mini_cap_var.trace_add('write', save_settings_now)
    esuisc_var.trace_add('write', save_settings_now)
    dockres_var.trace_add('write', save_settings_now)
    title_var.trace_add('write', save_settings_now)
    theme_var.trace_add('write', save_settings_now)
    theme_var.trace_add('write',
                        lambda *_a: root.after_idle(on_theme_change))
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
    texcache_var.trace_add('write', save_settings_now)
    frame_var.trace_add('write', save_settings_now)
    shadows_var.trace_add('write', save_settings_now)
    nocheat_var.trace_add('write', save_settings_now)
    witem_var.trace_add('write', save_settings_now)
    facial_var.trace_add('write', save_settings_now)
    autosize_var.trace_add('write', save_settings_now)
    compact_var.trace_add('write', save_settings_now)
    lim_var.trace_add('write', save_settings_now)
    # The four added with the field-buffer work. Without these they are
    # only written on Build and on window close, which is enough to be
    # correct and not enough to be obvious -- every other control here
    # saves the moment it changes.
    fbuf_var.trace_add('write', save_settings_now)
    gfx_var.trace_add('write', save_settings_now)
    scaler_var.trace_add('write', save_settings_now)
    bp_var.trace_add('write', save_settings_now)
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

    # ---------- log: drawn on a Canvas, banner behind the text -----------
    # A Tk Text cannot put an image BEHIND its text, so the log is a Canvas:
    # assets/banner.png sits at the bottom of the stack in the bottom-right
    # corner, and the visible lines are drawn over it. Only the lines on
    # screen exist as canvas items, so a 50,000-line build log redraws as
    # fast as a 10-line one. It keeps what the Text did: follows the end
    # while you are at the bottom, wheel and scrollbar, drag to select whole
    # lines, Cmd/Ctrl+C to copy them, Cmd/Ctrl+A for everything, and a
    # right-click Copy / Copy all.
    LOG_FONT = tkfont.Font(family=MONO_FONT, size=11)
    LOG_LH = LOG_FONT.metrics('linespace')
    LOG_PAD = 4
    logbox = tk.Canvas(logframe, background=LOG_BG, highlightthickness=0,
                       bd=0, height=8 * LOG_LH + 2 * LOG_PAD,
                       takefocus=1)
    _log = {'lines': [], 'top': 0, 'follow': True, 'sel': None,
            'anchor': None, 'job': None}

    def _log_rows():
        return max(1, (logbox.winfo_height() - 2 * LOG_PAD) // LOG_LH)

    def _log_max_top():
        return max(0, len(_log['lines']) - _log_rows())

    def _log_render():
        _log['job'] = None
        lines, rows = _log['lines'], _log_rows()
        top = _log_max_top() if _log['follow'] else \
            min(max(0, _log['top']), _log_max_top())
        _log['top'] = top
        logbox.delete('logtxt', 'logsel')
        W = logbox.winfo_width()
        if _log['sel']:
            a, b = _log['sel']
            a, b = max(a, top), min(b, top + rows)
            if a <= b:
                logbox.create_rectangle(
                    0, LOG_PAD + (a - top) * LOG_LH, W,
                    LOG_PAD + (b - top + 1) * LOG_LH,
                    fill=BG_ROW_SEL, outline='', tags='logsel')
        for i in range(top, min(len(lines), top + rows + 1)):
            y = LOG_PAD + (i - top) * LOG_LH
            # a 1px dark copy first, so the text stays readable where it
            # crosses the bright banner
            logbox.create_text(LOG_PAD + 1, y + 1, text=lines[i],
                               anchor='nw', font=LOG_FONT, fill='#000000',
                               tags='logtxt')
            logbox.create_text(LOG_PAD, y, text=lines[i], anchor='nw',
                               font=LOG_FONT, fill=LOG_TEXT, tags='logtxt')
        logbox.tag_lower('banner')
        n = len(lines)
        logscroll.set(*((top / n, min(1.0, (top + rows) / n)) if n
                        else (0.0, 1.0)))

    def _log_soon(*_a):
        if _log['job'] is None:
            _log['job'] = logbox.after_idle(_log_render)

    def _log_scroll_to(top):
        _log['top'] = min(max(0, int(top)), _log_max_top())
        _log['follow'] = _log['top'] >= _log_max_top()
        _log_soon()

    def _log_yview(*args):
        if not args:
            return
        if args[0] == 'moveto':
            _log_scroll_to(float(args[1]) * len(_log['lines']))
        elif args[0] == 'scroll':
            k = int(args[1]) * (_log_rows() if args[2] == 'pages' else 1)
            _log_scroll_to(_log['top'] + k)

    logscroll = ttk.Scrollbar(logframe, orient='vertical',
                              command=_log_yview)
    logscroll.pack(side='right', fill='y')
    logbox.pack(side='left', fill='both', expand=True)

    def _log_wheel(event):
        if getattr(event, 'num', None) in (4, 5):
            step = -3 if event.num == 4 else 3
        else:
            d = event.delta
            # Windows reports 120 a notch, macOS a few units
            step = -max(1, abs(d) // 40) if d > 0 else max(1, abs(d) // 40)
            if sys.platform == 'darwin':
                step = -1 if d > 0 else 1
        _log_scroll_to(_log['top'] + step)
        return 'break'

    logbox.bind('<MouseWheel>', _log_wheel)
    logbox.bind('<Button-4>', _log_wheel)
    logbox.bind('<Button-5>', _log_wheel)

    def _log_line_at(y):
        i = _log['top'] + (y - LOG_PAD) // LOG_LH
        return min(max(0, i), max(0, len(_log['lines']) - 1))

    def _log_press(event):
        logbox.focus_set()
        if not _log['lines']:
            return
        # a plain click only clears; dragging is what selects
        _log.update(anchor=_log_line_at(event.y), sel=None)
        _log_soon()

    def _log_drag(event):
        if _log['anchor'] is None or not _log['lines']:
            return
        if event.y < 0:
            _log_scroll_to(_log['top'] - 1)
        elif event.y > logbox.winfo_height():
            _log_scroll_to(_log['top'] + 1)
        i, a = _log_line_at(event.y), _log['anchor']
        _log['sel'] = (min(a, i), max(a, i))
        _log_soon()

    def _log_copy(_evt=None, every=False):
        lines = _log['lines']
        if every:
            chosen = lines
        elif _log['sel']:
            a, b = _log['sel']
            chosen = lines[a:b + 1]
        else:
            return 'break'
        root.clipboard_clear()
        root.clipboard_append('\n'.join(chosen))
        return 'break'

    def _log_select_all(_evt=None):
        if _log['lines']:
            _log['sel'] = (0, len(_log['lines']) - 1)
            _log_soon()
        return 'break'

    def _log_clear_sel(_evt=None):
        _log.update(sel=None, anchor=None)
        _log_soon()

    logbox.bind('<Button-1>', _log_press)
    logbox.bind('<B1-Motion>', _log_drag)
    logbox.bind('<Escape>', _log_clear_sel)
    _mod = 'Command' if sys.platform == 'darwin' else 'Control'
    logbox.bind('<%s-c>' % _mod, _log_copy)
    logbox.bind('<%s-a>' % _mod, _log_select_all)

    log_menu = tk.Menu(logbox, tearoff=0)
    log_menu.add_command(label='Copy', command=_log_copy)
    log_menu.add_command(label='Copy all',
                         command=lambda: _log_copy(every=True))

    def _log_menu(event):
        log_menu.tk_popup(event.x_root, event.y_root)
    logbox.bind('<Button-3>', _log_menu)
    if sys.platform == 'darwin':
        logbox.bind('<Button-2>', _log_menu)
        logbox.bind('<Control-Button-1>', _log_menu)

    def log_write(text):
        _log['lines'].extend(str(text).split('\n'))
        _log_soon()

    def log_clear():
        _log.update(lines=[], top=0, follow=True, sel=None, anchor=None)
        _log_soon()

    # ---------- banner in the log's bottom-right corner -------------------
    # assets/banner.png with a soft drop shadow, sized to the log's height
    # (at most BANNER_MAX_H). The lowest item on the log canvas, so the
    # text always draws over it.
    BANNER_PATH = os.path.join(nxpaths.APP, 'assets', 'banner.png')
    BANNER_MAX_H, BANNER_MARGIN = 150, 8
    _banner = {'src': None, 'img': None, 'size': None}

    def _banner_image(h):
        """The banner at height `h`, shadow included, keeping its alpha."""
        try:
            from PIL import Image as _BImg, ImageFilter as _BFlt, \
                ImageTk as _BImgTk
            if _banner['src'] is None:
                _banner['src'] = _BImg.open(BANNER_PATH).convert('RGBA')
            src = _banner['src']
            pad, off = 6, (2, 3)    # room for the blur; shadow offset
            ih = h - 2 * pad
            iw = max(1, round(src.width * ih / src.height))
            art = src.resize((iw, ih), getattr(_BImg, 'LANCZOS',
                                               _BImg.BICUBIC))
            out = _BImg.new('RGBA', (iw + 2 * pad, h), (0, 0, 0, 0))
            alpha = art.getchannel('A').point(lambda a: a * 0.55)
            out.paste((0, 0, 0, 255), (pad + off[0], pad + off[1]), alpha)
            out = out.filter(_BFlt.GaussianBlur(3))
            out.alpha_composite(art, (pad, pad))
            return _BImgTk.PhotoImage(out)
        except Exception:                                   # noqa: BLE001
            return None

    def _log_configure(_evt=None):
        W, H = logbox.winfo_width(), logbox.winfo_height()
        h = min(BANNER_MAX_H, H - 2 * BANNER_MARGIN)
        logbox.delete('banner')
        if h >= 40 and os.path.exists(BANNER_PATH):
            if _banner['size'] != h:
                img = _banner_image(h)
                _banner.update(img=img, size=h if img else None)
            if _banner['img'] is not None:
                logbox.create_image(W - BANNER_MARGIN, H - BANNER_MARGIN,
                                    image=_banner['img'], anchor='se',
                                    tags='banner')
        _log_render()

    logbox.bind('<Configure>', _log_configure)

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

    # BUILD 849: the pane's title carries a refresh icon (re-read mods/),
    # and a "Restore Priority" pill sits on the same line at the right.
    mods_hdr = ttk.Frame(panes)
    mods_title = ttk.Label(mods_hdr, text='Mods', foreground=TEXT_SECONDARY,
                           font=(UI_FONT, 10, 'bold'))
    mods_title.pack(side='left')
    mods_refresh = ttk.Label(mods_hdr, padding=0,
                             text='\u21bb', foreground=TEXT_SECONDARY)
    mods_refresh.pack(side='left', padx=(6, 0))
    left = ttk.Labelframe(panes, labelwidget=mods_hdr, padding=8)
    panes.add(left, weight=1)
    _canvas, listframe = make_scrollable(left, inner_width_tracks_canvas=True)

    PILL_FONT = (UI_FONT, 9, 'bold')
    _pillf = tkfont.Font(font=PILL_FONT)
    PILL_H = _pillf.metrics('linespace') + 6

    # BUILD 859: the pills are hidden, not greyed, when they do not apply.
    # They are `place`d over the frame's title line, so showing or hiding one
    # never moves the title, the refresh icon or the boxes below.
    def _make_pill(frame, hdr, text):
        w_ = _pillf.measure(text) + 2 * 11
        c = tk.Canvas(frame, width=w_, height=PILL_H, bg=BG_APP, bd=0,
                      highlightthickness=0)
        st = {'on': True, 'hover': False, 'shown': False, 'text': text,
              'w': w_, 'frame': frame, 'hdr': hdr}

        def draw():
            c.delete('all')
            c.configure(bg=BG_APP)
            on, hov = st['on'], st['hover']
            edge = ACCENT if on else BORDER
            fill = (ACCENT if hov else BG_APP) if on else BG_APP
            ink = (ON_ACCENT if hov else ACCENT_SOFT) if on else TEXT_MUTED
            r = PILL_H - 1
            w = w_ - 1
            for x0 in (0, w - r):
                c.create_oval(x0, 0, x0 + r, r, fill=fill, outline=edge)
            c.create_rectangle(r / 2.0, 0, w - r / 2.0, r, fill=fill,
                               outline='')
            c.create_line(r / 2.0, 0, w - r / 2.0, 0, fill=edge)
            c.create_line(r / 2.0, r, w - r / 2.0, r, fill=edge)
            c.create_text(w_ / 2.0, PILL_H / 2.0, text=text,
                          font=PILL_FONT, fill=ink)
            c.configure(cursor='')

        def hover(flag):
            st['hover'] = flag
            draw()

        def show(flag):
            # on the title line, right edge: measured from the frame's OUTER
            # edge (the default, inside the border, starts below the title),
            # centred on the title's line
            if flag and not st['shown']:
                c.place(in_=frame, bordermode='outside', relx=1.0, x=0,
                        y=max(0, (hdr.winfo_reqheight() - PILL_H) // 2),
                        anchor='ne')
            elif not flag and st['shown']:
                c.place_forget()
                st['hover'] = False
            st['shown'] = bool(flag)
            draw()

        c.bind('<Enter>', lambda e: hover(True))
        c.bind('<Leave>', lambda e: hover(False))
        return c, st, draw, show

    PILL_TEXT = 'Restore priority'
    pill, pill_state, _draw_prio, _show_prio = _make_pill(
        left, mods_hdr, PILL_TEXT)
    PILL_W = pill_state['w']
    _draw_prio()

    # Options pane, scrollable (mods can have a dozen-plus options).
    # Built like the Mods title (a frame, not `text=`), with a blank image
    # the size of the refresh icon, so both titles sit on one line and both
    # boxes start at the same height.
    opts_hdr = ttk.Frame(panes)
    ttk.Label(opts_hdr, text='Options', foreground=TEXT_SECONDARY,
              font=(UI_FONT, 10, 'bold')).pack(side='left')
    opts_spacer = ttk.Label(opts_hdr, padding=0, text='\u21bb',
                            foreground=BG_APP)
    opts_spacer.pack(side='left', padx=(6, 0))
    right = ttk.Labelframe(panes, labelwidget=opts_hdr, padding=6)
    panes.add(right, weight=2)
    # BUILD 859: "Restore defaults" for the selected mod, the Options twin of
    # "Restore priority"; shown only while there are mods
    dpill, dpill_state, _draw_dflt, _show_dflt = _make_pill(
        right, opts_hdr, 'Restore defaults')

    def _draw_pill():
        _draw_prio()
        _draw_dflt()
    _opt_canvas, options_body = make_scrollable(
        right, inner_width_tracks_canvas=True)

    # Shown, centred in both panes, while there are no mods; reload_mods
    # hides it once one arrives.
    _empty_notes = [ttk.Label(pane, text='Install mods to start.',
                              style='Sub.TLabel')
                    for pane in (left, right)]

    def show_empty_notes():
        for note in _empty_notes:
            if mods:
                note.place_forget()
            else:
                note.place(relx=0.5, rely=0.5, anchor='center')
        _show_dflt(bool(mods))

    selected = tk.StringVar(value=mods[0].filename if mods else '')

    # "Modifies": what a mod or option changes in the game, from
    # ff7nx_modifies (the mod's file list routed as the build routes it).
    # Exact categories in the accent colour, guessed ones (no dump) muted.
    def _field_bg_on():
        return current_field_bg_page_px() != build.ff7nx_fieldbg.OFF_PAGE_PX

    def modifies_label(parent, result, wraplength=220, **grid_kw):
        cats, _unused, guessed = result
        lbl = ttk.Label(parent, text=ff7nx_modifies.text(*result) or '—',
                        wraplength=wraplength, justify='left',
                        style='ArchiveEst.TLabel' if guessed or not cats
                        else 'Archive.TLabel')
        lbl.grid(**grid_kw)
        return lbl

    # ---------- mod details (what 7th Heaven's preview panel shows) ------
    # Description, release notes and the mod's own links from mod.xml
    # (iro.Manifest reads and cleans them). Prose goes in a read-only Text
    # rather than a Label because a Text can carry clickable spans: every
    # http(s):// or www. address inside the prose becomes a link. The Text
    # is sized to its own wrapped line count, so it scrolls with the panel
    # and never on its own.
    _URL = re.compile(r'(?:https?://|www\.)[^\s<>"\']+')
    DETAIL_FONT = (UI_FONT, 11)

    def open_url(url):
        import webbrowser
        webbrowser.open(url if '://' in url else 'http://' + url)

    def _fit_text(t):
        r = t.count('1.0', 'end-1c', 'displaylines')
        n = r[0] if isinstance(r, tuple) else (r or 0)
        if int(t.cget('height')) != n + 1:
            t.configure(height=n + 1)

    def detail_text(parent, text, fg=TEXT_PRIMARY):
        # padx=2 is the inset every ttk.Label around it has (clam's TLabel
        # layout: a 1px Label.border around a 1px Label.padding); without
        # it this text starts 2px left of "Modifies" and the headings.
        t = tk.Text(parent, wrap='word', height=1, width=1, font=DETAIL_FONT,
                    bg=BG_APP, fg=fg, bd=0, highlightthickness=0, padx=2,
                    pady=0, relief='flat', cursor='arrow', insertwidth=0,
                    selectbackground=BG_ROW_SEL, selectforeground=fg,
                    inactiveselectbackground=BG_ROW_SEL, takefocus=0)
        t.tag_configure('link', foreground=ACCENT_SOFT, underline=True)
        t.tag_bind('link', '<Enter>', lambda e: t.configure(cursor='arrow'))
        t.tag_bind('link', '<Leave>', lambda e: t.configure(cursor='arrow'))
        pos = 0
        for i, m in enumerate(_URL.finditer(text)):
            url = m.group(0).rstrip('.,;:!?)]}')
            t.insert('end', text[pos:m.start()])
            tag = 'link%d' % i
            t.insert('end', url, ('link', tag))
            t.tag_bind(tag, '<Button-1>', lambda e, u=url: open_url(u))
            pos = m.start() + len(url)
        t.insert('end', text[pos:])
        # disabled still lets the text be selected and copied
        t.configure(state='disabled')
        t.bind('<Configure>', lambda e: _fit_text(t))
        return t

    def _readme(folder):
        """7th Heaven's Readme button: readme.md, .html or .txt at the mod's
        root, in that order, whatever the case."""
        try:
            names = {n.lower(): n for n in os.listdir(folder)}
        except OSError:
            return None
        for want in ('readme.md', 'readme.html', 'readme.txt'):
            if want in names:
                return os.path.join(folder, names[want])
        return None

    def show_details(m):
        links = []
        if m.link and _URL.match(m.link):
            links.append(('Mod page', m.link))
        readme = _readme(m.folder)
        if readme:
            import pathlib
            links.append(('Readme', pathlib.Path(readme).as_uri()))
        if m.donation_link and _URL.match(m.donation_link):
            links.append(('Donate', m.donation_link))
        if links:
            row = ttk.Frame(options_body)
            row.pack(anchor='w', pady=(2, 6))
            for i, (label, url) in enumerate(links):
                lbl = ttk.Label(row, text=label, style='Link.TLabel',
                                cursor='')
                lbl.pack(side='left', padx=(0 if i == 0 else 14, 0))
                lbl.bind('<Button-1>', lambda e, u=url: open_url(u))
        if m.description:
            detail_text(options_body, m.description).pack(
                fill='x', pady=(2, 8))
        if m.release_notes:
            ttk.Label(options_body, text='RELEASE NOTES',
                      style='ColHeader.TLabel').pack(anchor='w')
            detail_text(options_body, m.release_notes,
                        fg=TEXT_SECONDARY).pack(fill='x', pady=(2, 8))
        if links or m.description or m.release_notes:
            # One pixel, half the ttk.Separator under the OPTION / VALUE
            # header (clam draws that one 2px), so the two lines do not
            # read as the same kind of break.
            tk.Frame(options_body, height=1, bg=BORDER, bd=0,
                     highlightthickness=0).pack(fill='x', pady=(2, 8))

    def _opt_label(label):
        """An option label as shown: ON / OFF (any case) read On / Off, as
        Bool options already do. Display only -- values are matched back
        through this same function, and settings keep the numbers."""
        word = label.strip()
        return {'on': 'On', 'off': 'Off'}.get(word.lower(), label)

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
                  font=(UI_FONT, 13, 'bold')).pack(anchor='w')
        meta = ' · '.join(x for x in (
            m.author, m.version, m.category,
            m.release_date and 'released ' + m.release_date) if x)
        if meta:
            ttk.Label(options_body, text=meta, style='Sub.TLabel'
                      ).pack(anchor='w', pady=(0, 2))
        _pc_only = ff7nx_esui.pc_only(mod)
        if _pc_only:
            # BUILD 849: shown, never built (see ff7nx_esui.PC_ONLY)
            _warn = ttk.Label(options_body, text=_pc_only[1],
                              foreground=WARN, wraplength=420,
                              justify='left')
            _warn.pack(anchor='w', fill='x', pady=(2, 8))
            _warn.bind('<Configure>', lambda e, w=_warn: w.configure(
                wraplength=max(200, e.width - 4)))
        show_details(m)
        store = settings_by_mod.setdefault(mod.filename, {})
        summary = ttk.Frame(options_body)
        summary.pack(anchor='w', fill='x', pady=(2, 10))
        summary.columnconfigure(1, weight=1)
        ttk.Label(summary, text='Modifies (as configured): ',
                  style='Sub.TLabel').grid(row=0, column=0, sticky='nw')
        summary_lbl = ttk.Label(summary, wraplength=460, justify='left')
        summary_lbl.grid(row=0, column=1, sticky='w')
        summary_note = ttk.Label(summary, style='Sub.TLabel',
                                 font=(UI_FONT, 9), wraplength=600,
                                 justify='left')

        def refresh_summary():
            cats, unused, guessed = result = ff7nx_modifies.for_mod(
                mod, store, catalogs, _field_bg_on())
            summary_lbl.configure(
                text=ff7nx_modifies.text(*result) or '—',
                style='ArchiveEst.TLabel' if guessed or not cats
                else 'Archive.TLabel')
            notes = []
            if unused and cats:
                notes.append('%d file(s) in this selection are PC-only and '
                             'not used on the Switch.' % unused)
            if guessed:
                notes.append('(est.) is guessed from folder names; point '
                             'this app at your game dump for exact matches.')
            summary_note.configure(text='  '.join(notes))
            if notes:
                summary_note.grid(row=1, column=0, columnspan=2, sticky='w',
                                  pady=(2, 0))
            else:
                summary_note.grid_remove()

        refresh_summary()
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
            labels = [_opt_label(label) for _, label in opt.values]
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

            var = tk.StringVar(value=_opt_label(opt.label_for(current)))
            _clb = any(v.get() for f, v in enabled.items()
                       if 'limitbreak' in f.lower().replace(' ', '').replace('_', ''))
            locked = (ff7nx_esui.locked_option(mod, opt.id)
                      or build.ff7nx_nitpick.locked_option(mod, opt.id, clb=_clb))
            if locked and build.ff7nx_nitpick.locked_value(mod, opt.id, clb=_clb) is not None:
                # BUILD 641: shown at the value the build applies
                current = build.ff7nx_nitpick.locked_value(mod, opt.id, clb=_clb)
                var.set(_opt_label(opt.label_for(current)))
            combo = ttk.Combobox(grid, textvariable=var, values=labels,
                                 state='disabled' if locked else 'readonly',
                                 width=16)
            combo.grid(row=row, column=1, sticky='ew', pady=3, padx=(0, 14))
            if locked:
                # BUILD 629: shown, greyed out -- the Switch build has its
                # own version of what this option changes on PC
                ttk.Label(grid, text=locked, style='Sub.TLabel',
                          wraplength=MODIFIES_COL - 10).grid(
                    row=row, column=2, sticky='w', padx=(14, 0))
                store.setdefault(opt.id, current)
                continue

            modifies_label(grid, ff7nx_modifies.for_option(
                mod, opt, store, catalogs, _field_bg_on()),
                wraplength=MODIFIES_COL - 10,
                row=row, column=2, sticky='w', padx=(14, 0))

            def on_pick(_evt, o=opt, v=var, mf=mod.filename):
                for value, label in o.values:
                    if _opt_label(label) == v.get():
                        settings_by_mod.setdefault(mf, {})[o.id] = value
                        break
                save_settings_now()
                refresh_summary()
                _sync_dflt()

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
    _all_checks = weakref.WeakSet()     # every _Check, for apply_theme

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
        r = r * n / 13.0                # the corner scales with the box too
        S = n * ss
        im = Image.new('RGB', (S, S), bg)
        d = ImageDraw.Draw(im)
        if on:
            d.rounded_rectangle([0, 0, S - 1, S - 1], radius=r * ss,
                                fill=ACCENT)
            # BUILD 833: _CHK_TICK is drawn for the 13px macOS box. The box
            # follows the font's cap height, so on Windows it is ~16px and an
            # unscaled tick sat small and thin in its top-left corner. Scale
            # the tick, its stroke and the corner about the box instead.
            k = n / 13.0
            d.line([(x * k * ss, y * k * ss) for x, y in _CHK_TICK],
                   fill=CHECK_TICK, width=max(2 * ss, int(round(2 * k * ss))),
                   joint='curve')
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
                             padx=0, pady=0, cursor='')
            self._var = variable
            self._bg = bg
            self._command = command
            _all_checks.add(self)
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
                c.create_line(ax, ay, bx, by, fill=CHECK_TICK, width=2,
                              capstyle='round')
                c.create_line(bx, by, cx, cy, fill=CHECK_TICK, width=2,
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
                        font=(UI_FONT, 12, 'bold' if bold else 'normal'))

    def _blend(a, b, t):
        """Colour `t` of the way from `a` to `b` (both #rrggbb)."""
        ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
        cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
        return '#%02x%02x%02x' % tuple(int(round(x + (y - x) * t))
                                       for x, y in zip(ca, cb))

    def _moved_bg(hover=False):
        # BUILD 849: a dragged mod's backdrop -- the row colour tinted toward
        # the theme's accent, so a hand-placed mod is never mistaken for one
        # in its computed place, in every theme's own colour.
        return _blend(BG_ROW, ACCENT, 0.17 if hover else 0.11)

    def _restyle(filename):
        if filename not in rows:
            return
        if filename == selected.get():
            _paint_row(filename, BG_ROW_SEL, ACCENT_SOFT, True, ACCENT)
        elif filename in moved:
            bg = _moved_bg()
            _paint_row(filename, bg, TEXT_PRIMARY, False, bg)
        else:
            _paint_row(filename, BG_ROW, TEXT_PRIMARY, False, BG_ROW)

    def select(mod):
        prev = selected.get()
        selected.set(mod.filename)
        if prev and prev != mod.filename:
            _restyle(prev)
        _restyle(mod.filename)
        _sync_pill()
        show_options(mod)
        _sync_dflt()

    def _sync_pill():
        # BUILD 859: shown only on a mod outside the supported list (the
        # only ones that can be dragged); live while it has been moved,
        # shaded and inert while it sits where the program put it
        fn = selected.get()
        mod = next((m for m in mods if m.filename == fn), None)
        if mod is None or not _draggable(mod):
            _show_prio(False)
            return
        pill_state['on'] = fn in moved
        _show_prio(True)

    def on_row_enter(filename):
        if drag['active']:
            return
        if filename != selected.get():
            bg = _moved_bg(True) if filename in moved else BG_ROW_HOVER
            _paint_row(filename, bg, TEXT_PRIMARY, False, bg)

    def on_row_leave(filename):
        if drag['active']:
            return
        if filename != selected.get():
            _restyle(filename)

    # ---------- BUILD 849: press, hold and drag ----------
    # Only a mod data/mod_sources.json does not list can be dragged: the
    # supported mods have a tested place. Hold the button DRAG_HOLD_MS on
    # the row, then move: a line shows where the mod will land; release to
    # drop it there. The mod is then marked moved (highlighted, saved) until
    # "Restore Priority" puts it back.
    DRAG_HOLD_MS = 350
    drag = {'mod': None, 'timer': None, 'active': False, 'to': None,
            'line': None}

    def _draggable(mod):
        return mod.filename not in reference_listed(mods)

    def _drag_press(event, mod):
        _drag_cancel()
        if build_btn.instate(['disabled']) or not _draggable(mod):
            return
        drag['mod'] = mod
        drag['timer'] = root.after(DRAG_HOLD_MS, lambda: _drag_start(mod))

    def _drag_start(mod):
        drag['timer'] = None
        if drag['mod'] is not mod or mod.filename not in rows:
            return
        drag['active'] = True
        row = rows[mod.filename][0]
        for w in rows[mod.filename]:
            try:
                w.configure(cursor='fleur')
            except tk.TclError:
                pass
        line = tk.Frame(listframe, height=2, bg=ACCENT, bd=0,
                        highlightthickness=0)
        drag['line'] = line
        _paint_row(mod.filename, BG_ROW_SEL, ACCENT_SOFT, True, ACCENT)
        _drag_motion_at(row.winfo_pointery())

    def _drag_target(y_root):
        """Index in `mods` the dragged mod would take, and the y (in the
        list) of the line that shows it."""
        names = [m.filename for m in mods if m.filename in rows]
        idx, line_y = len(names), None
        for i, fn in enumerate(names):
            r = rows[fn][0]
            top = r.winfo_rooty()
            if y_root < top + r.winfo_height() / 2.0:
                idx = i
                line_y = r.winfo_y() - 3
                break
        if line_y is None and names:
            last = rows[names[-1]][0]
            line_y = last.winfo_y() + last.winfo_height() + 1
        return idx, max(0, line_y or 0)

    def _drag_motion_at(y_root):
        idx, line_y = _drag_target(y_root)
        drag['to'] = idx
        if drag['line'] is not None:
            drag['line'].place(x=0, y=line_y, relwidth=1.0)
            drag['line'].lift()
        # scroll when held near the list's top or bottom edge
        try:
            top = _canvas.winfo_rooty()
            bottom = top + _canvas.winfo_height()
            if y_root < top + 18:
                _canvas.yview_scroll(-1, 'units')
            elif y_root > bottom - 18:
                _canvas.yview_scroll(1, 'units')
        except tk.TclError:
            pass

    def _drag_motion(event):
        if drag['active']:
            _drag_motion_at(event.y_root)

    def _drag_cancel():
        if drag['timer'] is not None:
            try:
                root.after_cancel(drag['timer'])
            except tk.TclError:
                pass
        drag['timer'] = None
        if drag['line'] is not None:
            drag['line'].destroy()
        drag['line'] = None
        mod, was = drag['mod'], drag['active']
        drag.update(mod=None, active=False, to=None)
        if was and mod is not None and mod.filename in rows:
            for w in rows[mod.filename]:
                try:
                    w.configure(cursor='')
                except tk.TclError:
                    pass

    def _drag_release(event):
        mod, to, was = drag['mod'], drag['to'], drag['active']
        _drag_cancel()
        if not was or mod is None or to is None or mod not in mods:
            return
        frm = mods.index(mod)
        if to > frm:
            to -= 1
        if to == frm:
            _restyle(mod.filename)
            return
        mods.remove(mod)
        mods.insert(to, mod)
        moved.add(mod.filename)
        _repack_row(mod)
        log_write('Moved %s to priority %d of %d (by hand). Restore '
                  'Priority puts it back.'
                  % (mod.display_name, to + 1, len(mods)))
        save_settings_now()

    def _repack_row(mod):
        """Show `mod`'s row at its place in `mods` -- only that row moves,
        so nothing else is unpacked and redrawn."""
        if mod.filename not in rows:
            return
        row = rows[mod.filename][0]
        i = mods.index(mod)
        nxt = next((rows[m.filename][0] for m in mods[i + 1:]
                    if m.filename in rows), None)
        row.pack_forget()
        if nxt is not None:
            row.pack(fill='x', pady=(0, 4), before=nxt)
        else:
            row.pack(fill='x', pady=(0, 4))
        _restyle(mod.filename)
        _sync_pill()

    def restore_priority(_evt=None):
        """Put the selected mod back where the program would place it."""
        fn = selected.get()
        if fn not in moved or build_btn.instate(['disabled']):
            return
        mod = next((m for m in mods if m.filename == fn), None)
        moved.discard(fn)
        ref = apply_reference_order(_autosort_7h(list(mods)))
        mods[:] = apply_manual_moves(ref, [m.filename for m in mods], moved)
        if mod is not None:
            _repack_row(mod)
        if mod is not None:
            log_write('%s is back at its computed priority (%d of %d).'
                      % (mod.display_name, mods.index(mod) + 1, len(mods)))
        save_settings_now()

    pill.bind('<ButtonRelease-1>', lambda e: (
        restore_priority() if 0 <= e.x < PILL_W and 0 <= e.y < PILL_H
        else None))

    def _defaults_for(mod):
        entry = default_mod_settings().get(mod.filename)
        return _mod_options_from_saved(
            mod, {mod.filename: entry} if entry is not None else {}), \
            entry is not None

    def _sync_dflt():
        """BUILD 859: "Restore defaults" is live (accent) only while the
        selected mod's options differ from what it would restore."""
        mod = next((m for m in mods if m.filename == selected.get()), None)
        differs = False
        if mod is not None:
            want, _listed = _defaults_for(mod)
            have = settings_by_mod.get(mod.filename, {})
            differs = any(have.get(k, v) != v for k, v in want.items())
        if dpill_state['on'] != differs:
            dpill_state['on'] = differs
            _draw_dflt()

    def restore_selected_defaults(_evt=None):
        """BUILD 859: the selected mod's options back to their defaults --
        the tested settings in data/default_mod_settings.json for a
        supported mod, the mod's own (manifest) defaults otherwise. Its
        on/off tick is left as it is."""
        fn = selected.get()
        mod = next((m for m in mods if m.filename == fn), None)
        if mod is None or build_btn.instate(['disabled']) or \
                not dpill_state['on']:
            return
        settings_by_mod[fn], listed = _defaults_for(mod)
        show_options(mod)
        save_settings_now()
        _sync_dflt()
        log_write('%s: options restored to %s.' % (
            mod.display_name, 'the tested defaults' if listed
            else "the mod's own defaults"))

    dpill.bind('<ButtonRelease-1>', lambda e: (
        restore_selected_defaults()
        if 0 <= e.x < dpill_state['w'] and 0 <= e.y < PILL_H else None))

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

    ROW_FONT = (UI_FONT, 12)
    _rowf = tkfont.Font(font=ROW_FONT)
    _asc = _rowf.metrics('ascent')
    _desc = _rowf.metrics('descent')

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
    # options panel, which is Sub.TLabel over the default (UI_FONT, 11).
    VER_FONT = (UI_FONT, 11)
    _ver_y = _cap_band_y(VER_FONT)
    VER_PAD_R = 10
    _verf = tkfont.Font(font=VER_FONT)
    # sized to the widest label actually present, so the title gets every
    # remaining pixel rather than a guessed reserve
    _ver_w = max([_verf.measure(_ver_text(m)) for m in mods] or [0])

    # x follows the old padding: 4px indicator, then 10, the box, then 6
    _chk_x = 4 + 10
    _label_x = _chk_x + _CHK_SIZE + 6

    def add_mod_row(mod):
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
        # A row rebuilt by reload_mods keeps its tick.
        var = enabled.get(mod.filename)
        if var is None:
            var = tk.BooleanVar(value=saved_enabled(saved, mod))
            enabled[mod.filename] = var
            var.trace_add('write', save_settings_now)
        chk = _Check(row, var, BG_ROW)
        chk.place(x=_chk_x, y=_chk_y + _CHK_NUDGE)

        # bd/pady/highlightthickness zeroed so the label's box is exactly
        # the text, with nothing of its own to bias the centring
        label = tk.Label(row, text=mod.display_name, cursor='',
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
            # BUILD 849: press and hold to drag (unsupported mods only)
            widget.bind('<ButtonPress-1>',
                        lambda e, m=mod: _drag_press(e, m), add='+')
            widget.bind('<B1-Motion>', _drag_motion, add='+')
            widget.bind('<ButtonRelease-1>', _drag_release, add='+')

        if mod.filename not in settings_by_mod:
            settings_by_mod[mod.filename] = _mod_options_from_saved(mod, saved)

    for mod in mods:
        add_mod_row(mod)

    if mods:
        select(mods[0])
    show_empty_notes()

    # ---------- picking up new mods ----------
    # The list used to be read once, at startup, so a mod added by Install
    # Mods (or dropped into mods/ by hand) only showed after a restart.
    # reload_mods re-reads the folder and rebuilds the rows when what is
    # there changed. Ticks, options and order of the mods already listed
    # are kept; a new mod joins at the bottom with its factory entry, as at
    # startup. Not during a build, which reads `mods`: `done` calls it.
    def _mods_signature():
        try:
            names = sorted(os.listdir(MODS_DIR))
        except OSError:
            return ()
        sig = []
        for fn in names:
            if fn.startswith('.'):
                continue
            try:
                sig.append((fn, os.stat(os.path.join(MODS_DIR, fn)).st_mtime))
            except OSError:
                pass
        return tuple(sig)

    _mods_sig = [_mods_signature()]

    def reload_mods(force=False):
        nonlocal _ver_w
        if build_btn.instate(['disabled']):
            return False
        sig = _mods_signature()
        if not force and sig == _mods_sig[0]:
            return False
        _mods_sig[0] = sig
        had = {m.filename for m in mods}
        found = discover_mods([m.filename for m in mods]
                              or profile_mod_order(saved), moved)
        migrate_saved_mod_keys(saved, found)
        seed_mod_defaults(saved, found)
        for row_widgets in rows.values():
            row_widgets[0].destroy()
        rows.clear()
        mods[:] = found
        _ver_w = max([_verf.measure(_ver_text(m)) for m in mods] or [0])
        for mod in mods:
            add_mod_row(mod)
        cur = next((m for m in mods if m.filename == selected.get()), None)
        if cur is None and mods:
            cur = mods[0]
        if cur is not None:
            selected.set('')
            select(cur)
        else:
            selected.set('')
            show_options(None)
        show_empty_notes()
        count_label.configure(text=_count_text())
        added = [m.display_name for m in mods if m.filename not in had]
        if added:
            log_write('Added to the mod list: ' + ', '.join(added))
        if _ui_ready[0]:
            save_settings_now()
        return True

    # Picks up a mod copied in by hand once the user comes back to the window.
    root.bind('<FocusIn>', lambda e: reload_mods(), add='+')

    # BUILD 849: the refresh icon beside "Mods" re-reads mods/ on demand.
    def _mods_refresh(_evt=None):
        if build_btn.instate(['disabled']):
            log_write('Wait for the build to finish before refreshing the '
                      'mod list.')
            return 'break'
        before = [m.filename for m in mods]
        reload_mods(force=True)
        if [m.filename for m in mods] == before:
            log_write('Mod list refreshed: no new or removed mods.')
        return 'break'

    mods_refresh.bind('<Button-1>', _mods_refresh)

    def _redraw_mods_refresh():
        img = _refresh_image()
        if img is not None:
            mods_refresh.configure(image=img, text='')
            mods_refresh.image = img
            # the Options title's twin: a transparent image the same size
            blank = tk.PhotoImage(width=img.width(), height=img.height())
            opts_spacer.configure(image=blank, text='')
            opts_spacer.image = blank

    root.after_idle(_redraw_mods_refresh)

    # ---------- theme, live ----------
    # Switching used to need a restart because the colours were read once.
    # Now: rebind the palette names (everything draws from them at draw
    # time), re-run the ttk styles, then walk every Tk widget -- through Tcl,
    # so the Combobox dropdowns and open dialogs are included -- and swap
    # each old palette colour for the new one, in widget options, Canvas
    # items and Text tags. What keeps a colour of its own (the checkbox
    # images, the tinted header icons, the mod rows) is redrawn after.
    _THEME_KEYS = ('BG_APP', 'BG_ROW', 'BG_ROW_HOVER', 'BG_ROW_SEL',
                   'BORDER', 'ACCENT', 'ACCENT_SOFT', 'ACCENT_HOVER',
                   'ON_ACCENT', 'CHECK_TICK', 'TEXT_PRIMARY',
                   'TEXT_SECONDARY', 'TEXT_MUTED', 'LOG_BG', 'LOG_TEXT',
                   'GOOD', 'WARN')
    _WIDGET_COLOURS = ('-background', '-foreground', '-activebackground',
                       '-activeforeground', '-highlightbackground',
                       '-highlightcolor', '-selectbackground',
                       '-selectforeground', '-insertbackground',
                       '-disabledforeground', '-troughcolor',
                       '-selectcolor', '-readonlybackground',
                       '-disabledbackground')

    def apply_theme(key):
        nonlocal BG_APP, BG_ROW, BG_ROW_HOVER, BG_ROW_SEL, BORDER, ACCENT, \
            ACCENT_HOVER, ACCENT_SOFT, ON_ACCENT, CHECK_TICK, TEXT_PRIMARY, \
            TEXT_SECONDARY, TEXT_MUTED, ARCHIVE_EXACT, ARCHIVE_EST, LOG_BG, \
            LOG_TEXT, GOOD, WARN
        old = {'BG_APP': BG_APP, 'BG_ROW': BG_ROW,
               'BG_ROW_HOVER': BG_ROW_HOVER, 'BG_ROW_SEL': BG_ROW_SEL,
               'BORDER': BORDER, 'ACCENT': ACCENT,
               'ACCENT_HOVER': ACCENT_HOVER, 'ACCENT_SOFT': ACCENT_SOFT,
               'ON_ACCENT': ON_ACCENT, 'CHECK_TICK': CHECK_TICK,
               'TEXT_PRIMARY': TEXT_PRIMARY,
               'TEXT_SECONDARY': TEXT_SECONDARY, 'TEXT_MUTED': TEXT_MUTED,
               'LOG_BG': LOG_BG, 'LOG_TEXT': LOG_TEXT, 'GOOD': GOOD,
               'WARN': WARN}
        new = nxthemes.palette(key)
        _theme_now[0] = key
        # Two names can share a colour in one theme and not in the next;
        # the first name in _THEME_KEYS wins, which favours the colours a
        # widget rests in over the ones it only flashes.
        swap = {}
        for k in _THEME_KEYS:
            swap.setdefault(old[k].lower(), new[k])
        BG_APP, BG_ROW = new['BG_APP'], new['BG_ROW']
        BG_ROW_HOVER, BG_ROW_SEL = new['BG_ROW_HOVER'], new['BG_ROW_SEL']
        BORDER, ACCENT = new['BORDER'], new['ACCENT']
        ACCENT_HOVER, ACCENT_SOFT = new['ACCENT_HOVER'], new['ACCENT_SOFT']
        ON_ACCENT, CHECK_TICK = new['ON_ACCENT'], new['CHECK_TICK']
        TEXT_PRIMARY = new['TEXT_PRIMARY']
        TEXT_SECONDARY, TEXT_MUTED = new['TEXT_SECONDARY'], new['TEXT_MUTED']
        ARCHIVE_EXACT, ARCHIVE_EST = ACCENT_SOFT, TEXT_MUTED
        LOG_BG, LOG_TEXT = new['LOG_BG'], new['LOG_TEXT']
        GOOD, WARN = new['GOOD'], new['WARN']
        _apply_styles()

        call = root.tk.call

        def remap(value):
            return swap.get(str(value).lower()) if value else None

        def walk(path):
            try:
                cls = str(call('winfo', 'class', path))
            except tk.TclError:
                return
            for opt in _WIDGET_COLOURS:
                try:
                    to = remap(call(path, 'cget', opt))
                    if to:
                        call(path, 'configure', opt, to)
                except tk.TclError:
                    pass        # ttk widgets, or not an option of this one
            if cls == 'Canvas':
                for item in root.tk.splitlist(call(path, 'find', 'all')):
                    for opt in ('-fill', '-outline'):
                        try:
                            to = remap(call(path, 'itemcget', item, opt))
                            if to:
                                call(path, 'itemconfigure', item, opt, to)
                        except tk.TclError:
                            pass
            elif cls == 'Text':
                for tag in root.tk.splitlist(call(path, 'tag', 'names')):
                    for opt in ('-foreground', '-background'):
                        to = remap(call(path, 'tag', 'cget', tag, opt))
                        if to:
                            call(path, 'tag', 'configure', tag, opt, to)
            for child in root.tk.splitlist(call('winfo', 'children', path)):
                walk(str(child))

        walk('.')
        try:
            _redraw_refresh()
            _redraw_mods_refresh()
            _draw_pill()
        except tk.TclError:
            pass
        _chk_cache.clear()
        for chk in list(_all_checks):
            try:
                chk._bg = remap(chk._bg) or chk._bg
                chk.configure(bg=chk._bg)
                chk._draw()
            except tk.TclError:
                pass
        try:
            for img, name in _tinted_icons:
                img.paste(_button_icon_art(name))
        except Exception:                                   # noqa: BLE001
            pass
        for fn in rows:
            _restyle(fn)
        _log_render()

    # ---------- build status ----------
    # The line beside the progress bar says what the build is doing NOW.
    # It used to show the label of the last determinate progress call and
    # nothing else, so after Echo-S's voice clips it read "Echo-S voice"
    # through every exe and module pass that followed. Two things move it:
    #
    #   progress(i, n, label), n > 0   a counted step: label and percentage,
    #                                  the bar determinate
    #   progress(0, 0, label)          a stage with no count (run_build and
    #                                  apply_plan call these between steps)
    #
    # and a log line that opens a pass -- unindented and ending in "..."
    # ("applying 60 FPS patches ...", "building flevel.lgp ..."). Every
    # pass already logs one, so a new pass is covered without touching this.
    _STAGE_TAG = re.compile(r'\s*\((?:BUILD|HANDOFF)[^)]*\)')

    def _status_text(label):
        label = label[:1].upper() + label[1:]
        return label if len(label) <= 60 else label[:59].rstrip() + '…'

    def set_stage(label):
        label = _STAGE_TAG.sub('', label).strip()
        if not label:
            return
        statuslabel.configure(text=_status_text(label))
        if str(bar['mode']) != 'indeterminate':
            bar.configure(mode='indeterminate', maximum=100)
            bar['value'] = 0
            bar.start(15)

    def set_count(i, n, label):
        if str(bar['mode']) != 'determinate':
            bar.stop()
            bar.configure(mode='determinate')
        bar['maximum'] = n
        bar['value'] = i
        label = _STAGE_TAG.sub('', label or '').strip()
        pct = int(100 * min(i, n) / n)
        statuslabel.configure(
            text='%s  %d%%' % (_status_text(label), pct)
            if label else '%d%%' % pct)

    def _stage_from_log(line):
        if not line or line[0].isspace() or line[0] in '!(':
            return None
        line = line.rstrip()
        if not line.endswith('...'):
            return None
        return line[:-3]

    # ---------- queue pump ----------
    def pump():
        try:
            while True:
                kind, payload = messages.get_nowait()
                if kind == 'log':
                    log_write(payload)
                    stage = _stage_from_log(payload)
                    if stage:
                        set_stage(stage)
                elif kind == 'progress':
                    i, n, label = payload
                    if n > 0:
                        set_count(i, n, label)
                    else:
                        set_stage(label or '')
                elif kind == 'done':
                    bar.stop()
                    bar.configure(mode='determinate')
                    build_btn.state(['!disabled'])
                    bar['value'] = bar['maximum'] if payload else 0
                    statuslabel.configure(text='done' if payload else 'failed')
                    # anything installed while the build ran
                    reload_mods()
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
        set_stage('Starting…')
        cap_value = current_field_tex_cap()
        world_cap_value = current_world_tex_cap()
        bg_cap_value = current_battle_bg_tex_cap()
        spell_cap_value = current_spell_tex_cap()
        mini_cap_value = current_minigame_tex_cap()
        spell_fx_value = current_spell_fx_cap()
        fbg_px_value = current_field_bg_page_px()
        # DEPTH-1 PAGE SIZE -- NOW A DIALOG CONTROL. FINDINGS-223, 225.
        #
        # This used to read from settings.json only, and the note here argued
        # that a dropdown "would imply a freedom it does not have" because
        # the size changes the ARCHIVE FORMAT and needs ten matching module
        # words. RETRACTED. The freedom is real: `build.py` emits those words
        # FROM this value and raises if the two disagree, so the pairing is
        # enforced rather than trusted. What the missing control actually
        # achieved was that the one knob governing fx-texture size -- and
        # therefore the one lever against the memory and bandwidth cost of
        # the 512px build -- was invisible to the person running the tool.
        #
        # The hazard the old note describes is still real and still worth
        # stating: wrong value and the loader reads 0x40000 bytes per
        # paletted page out of a file holding 0x10000, which is not a worse
        # picture but a desynchronised TEXTURE walk. It is prevented by the
        # two halves being emitted together, not by hiding the control.
        #
        # An env var set before launch still wins, so an A/B needs no edit.
        if build.ff7nx_fieldbg.D1_PX_ENV not in os.environ:
            os.environ[build.ff7nx_fieldbg.D1_PX_ENV] = str(
                current_field_bg_d1_px())
        # BUILD 109. Content only -- it cannot desynchronise anything,
        # because off is 2x replication and on is different PIXELS in a page
        # of the same size. That is why it is a plain bool while the size
        # above needs eight module words to agree with it.
        if build.field_bg_shadow.ENV not in os.environ:
            os.environ[build.field_bg_shadow.ENV] = (
                '1' if _global_setting('field_bg_d1_art', True) else '0')
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
            _global_setting('field_bg_clamp_palettes', False))
        # FINDINGS-136: a promoted cell keeps its grid coordinate.
        build.field_bg_dense.PRESERVE_CELL_COORDS = bool(
            _global_setting('field_bg_preserve_cell_coords', True))
        # FINDINGS-228: promote a cell that several palettes recolour, keyed
        # per palette, when its variants provably differ.
        #
        # THIS NO LONGER DRIVES `PROMOTE_FX_BASE`, AND THAT COUPLING COST A
        # BUILD. FINDINGS-232/234. The two flags were introduced together
        # because neither does anything without the other, so this line set
        # both -- which meant that when `PROMOTE_FX_BASE` was reverted to
        # False in the module to stop `wcrimb_2` crashing, THIS LINE PUT IT
        # BACK at build time and the rebuilt archive came out byte-identical.
        # A module constant that the GUI overwrites is not a constant, and a
        # revert that a settings default undoes is not a revert.
        #
        # `PROMOTE_FX_BASE` is now owned by `field_bg_dense` alone, and the
        # per-field experiment goes through `field_bg_fx_base_fields` below.
        build.field_bg_dense.MULTIPAL_RECOLOUR = bool(
            _global_setting('field_bg_multipal', True))
        _fxb = _global_setting('field_bg_fx_base_fields', '')
        build.field_bg_dense.PROMOTE_FX_BASE_FIELDS = frozenset(
            s.strip().lower() for s in str(_fxb or '').split(',') if s.strip())
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
        os.environ[build.MOVIE_30FPS_ENV] = (
            '1' if _needs_nso(m30_var.get()) else '0')
        os.environ[build.ANALOG_360_ENV] = (
            '1' if _needs_nso(a360_var.get()) else '0')
        os.environ[build.NO_AUTORUN_ENV] = (
            '1' if _needs_nso(norun_var.get()) else '0')
        os.environ[_HEADLESS_TEXCACHE_ENV] = current_texture_cache()
        os.environ[build.NO_CHEATS_ENV] = (
            '1' if _needs_nso(nocheat_var.get()) else '0')
        os.environ[build.WITEM_FIX_ENV] = '1' if witem_var.get() else '0'
        os.environ[build.ADV_FACIAL_ENV] = '1' if facial_var.get() else '0'
        os.environ[build.TEXT_AUTOSIZE_ENV] = (
            '1' if autosize_var.get() else '0')
        os.environ['SEVENTH_NX_COMPACT_CACHE'] = (
            '1' if compact_var.get() else '0')
        os.environ[build.LIMITER_FPS_ENV] = str(
            _needs_nso(current_limiter_fps(), 0))
        os.environ[build.ff7nx_ws.WIDESCREEN_ENV] = current_widescreen()
        # The FRAME HEIGHT and the MOVIE that has to meet it ride the
        # checkbox. They are one visual change and cannot be split --
        # moving the field without the movie makes the cut worse than not
        # doing either -- so they are one switch. Off means OFF even with
        # 16:9 selected.
        _ff = '1' if frame_var.get() else '0'
        os.environ[build.FIELD_FRAME_ENV] = _ff
        os.environ[build.MOVIE_ALIGN_ENV] = _ff
        os.environ['SEVENTH_NX_SHADOWS'] = '1' if shadows_var.get() else '0'
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
        # The graphics pool. Written unconditionally, including at stock,
        # so that clearing the dropdown back to 256 actually clears the
        # environment a previous run in this process left behind.
        #
        # The module-level constant, not the closed-over `_gfxpool` from
        # launch_ui. Both resolve, but this one does not depend on `start`
        # staying nested where it is, and it is the same name the headless
        # path uses. test_gfxpool.py asserts it matches
        # ff7nx_gfxpool.POOL_MB_ENV.
        os.environ[_HEADLESS_GFX_ENV] = str(current_gfx_pool())
        os.environ[build.voice_ogg.NORMALIZE_ENV] = current_voice_normalize()
        os.environ[build.ff7nx_shaders.SCALER_ENV] = current_scaler()
        os.environ[build.ff7nx_shaders.BLACK_POINT_ENV] = str(
            current_black_point())
        os.environ[build.ff7nx_shaders.FXAA_ENV] = current_fxaa()
        os.environ[build.ff7nx_shaders.VIDEO_ENV] = \
            current_video_shader()
        # Only written when it is not the default, so a 2x build keeps the
        # archive fingerprints every earlier build recorded.
        # BUILD 761: unset when off, so nothing about the build changes.
        os.environ['SEVENTH_NX_TITLE_SCREEN'] = current_title_screen()
        if current_dock_resolution():
            os.environ['SEVENTH_NX_DOCK_RES'] = current_dock_resolution()
        else:
            os.environ.pop('SEVENTH_NX_DOCK_RES', None)
        if current_esui_texscale() != 2:
            os.environ[ff7nx_esui.SCALE_ENV] = str(current_esui_texscale())
        else:
            os.environ.pop(ff7nx_esui.SCALE_ENV, None)
        build.save_settings(SETTINGS, snapshot_settings())
        os.environ[build.FIELD_TEX_CAP_ENV] = str(cap_value)
        if cap_value:
            log_write(f'field texture cap: {cap_value}px '
                      '(char.lgp model textures larger than '
                      'this will be downscaled)')
        os.environ[build.WORLD_TEX_CAP_ENV] = str(world_cap_value)
        if world_cap_value:
            log_write(f'world map texture cap: {world_cap_value}px '
                      '(world_us.lgp textures larger than this will be '
                      'downscaled; vanilla’s own maximum is 256px)')
        else:
            log_write('world map texture cap: OFF -- an HD world set can '
                      'decode to 53 MB paletted against a 64 MB heap')
        os.environ[build.BATTLE_BG_TEX_CAP_ENV] = str(bg_cap_value)
        if bg_cap_value != 256:
            log_write(f'Arisen battle background texture cap: '
                      f'{bg_cap_value}px (only tiles from Avalanche '
                      'Arisen -- everything else in battle.lgp stays at '
                      'the proven 256px)')
        os.environ[build.ff7nx_spelltex.CAP_ENV] = str(spell_cap_value)
        os.environ[build.ff7nx_spelltex.FX_CAP_ENV] = '0'
        os.environ[build.ff7nx_minigametex.CAP_ENV] = str(mini_cap_value)
        if mini_cap_value:
            log_write(f'SYW minigame texture cap: {mini_cap_value}px')
        else:
            log_write('SYW minigame textures: native size (no upscaling)')
        if spell_cap_value:
            log_write(f'spell texture cap: {spell_cap_value}px '
                      f'({spell_cap_value // 256}x vanilla for a 256px slot; '
                      'magic.lgp textures rebuilt from FFNx DDS only)')
        else:
            log_write('spell textures: native size (vanilla dimensions, '
                      'magic.lgp size unchanged)')
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
            build_busy['active'] = True
            try:
                ok = run_build(
                    mods,
                    {k: v.get() for k, v in enabled.items()},
                    settings_by_mod,
                    lambda s: messages.put(('log', s)),
                    lambda i, n, label='': messages.put(
                        ('progress', (i, n, label))),
                    fps_60=bool(_needs_nso(fps_var.get())))
            except Exception as exc:
                import traceback
                messages.put(('log', 'ERROR: ' + str(exc)))
                messages.put(('log', traceback.format_exc()))
            finally:
                build_busy['active'] = False
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

    def _initial_splits():
        root.update_idletasks()
        try:
            panes.sashpos(0, int(panes.winfo_width() * WIN_SPLIT_X))
            vsplit.sashpos(0, int(vsplit.winfo_height() * WIN_SPLIT_Y))
        except tk.TclError:
            pass
    root.after(50, _initial_splits)

    if DUMP is not None and dump_state['problems']:
        log_write('The game dump does not look right -- builds may fail:')
        for why in dump_state['problems']:
            log_write('  ! ' + why)
    if not mods:
        log_write(f'No .iro files found in {MODS_DIR}')
        log_write('Drop your 7th Heaven mods there, or use Install Mods; '
                  'the list picks them up on its own.')
    else:
        log_write('Ready. Tick the mods you want, choose options, '
                  'then Build SD Output.')
        if DEV_MODE:
            log_write('Developer mode: every setting is shown.')

    root.mainloop()


def selftest():
    """
    `--selftest`: check that this copy has everything it runs on, without
    opening the window or building -- every module findable, the packages
    and PyFF7 importable, the program's own files present and the user's
    folder writable. tools/release.py runs it on each finished release.
    Returns the exit status.
    """
    import importlib
    import importlib.util
    import ff7nx_shaders
    bad = []
    names = sorted({f[:-3] for f in nxpaths.code_files()
                    if f.endswith('.py') and f != '7th_heaven_nx.py'})
    for name in names:
        try:
            if importlib.util.find_spec(name) is None:
                bad.append('module %s: not found' % name)
        except Exception as exc:                                # noqa: BLE001
            bad.append('module %s: %s' % (name, exc))
    for name in ('numpy', 'scipy', 'PIL.Image', 'lz4.block', 'capstone',
                 'PyFF7.lgp', 'tkinter', 'lgp', 'build'):
        try:
            importlib.import_module(name)
        except Exception as exc:                                # noqa: BLE001
            bad.append('import %s: %s' % (name, exc))
    # BUILD 808: the modules that add folders to sys.path must not let one
    # outside the program replace a bundled module (release.py plants a
    # decoy arm64emu.py in this folder and its parent to prove it).
    for name in ('diag_common', 'ff7nx_letterbox', 'ff7nx_camclamp',
                 'cave_space', 'ws_emu', 'arm64emu'):
        try:
            importlib.import_module(name)
        except Exception as exc:                                # noqa: BLE001
            bad.append('import %s: %s' % (name, exc))
    for name in ('arm64emu', 'ws_emu', 'nxpaths'):
        mod = sys.modules.get(name)
        where = getattr(mod, '__file__', '') or ''
        if mod is None or not where or not nxpaths._inside_app(where):
            bad.append('module %s loaded from outside the program: %s'
                       % (name, where or '?'))
    if not hasattr(getattr(sys.modules.get('arm64emu'), 'Cpu', None),
                   'guest_to_host'):
        bad.append('arm64emu.Cpu has no guest_to_host (wrong arm64emu)')
    if nxpaths.FROZEN:
        stray = [p for p in sys.path if not nxpaths._inside_app(p)]
        if stray:
            bad.append('sys.path reaches outside the program: %s'
                       % ', '.join(stray[:3]))
    files = (['assets/icon.png', 'assets/banner.png',
              'data/default_settings.json', 'data/default_mod_settings.json',
              'data/mod_sources.json', 'data/battle_bg_dds_map.json']
             + ff7nx_shaders.release_files())
    for rel in files:
        if not os.path.isfile(os.path.join(nxpaths.APP, rel)):
            bad.append('missing %s' % rel)
    try:
        os.makedirs(HERE, exist_ok=True)
        probe = os.path.join(HERE, '.selftest')
        with open(probe, 'w') as f:
            f.write('ok')
        os.remove(probe)
    except OSError as exc:
        bad.append('folder %s is not writable: %s' % (HERE, exc))
    print('7th Heaven NX %s selftest' % VERSION)
    print('  program files: %s' % nxpaths.APP)
    print('  your folder:   %s' % HERE)
    print('  modules:       %d' % len(names))
    print('  LZS native:    %s' % ('yes' if lgp._native_lib() else
                                   'no (Python fallback, same output)'))
    for line in bad:
        print('  FAIL ' + line)
    print('  result:        %s' % ('ok' if not bad else 'FAILED'))
    return 1 if bad else 0


def main():
    if '--selftest' in sys.argv:
        return selftest()
    # The folders the user fills. Neither is in git or in a release zip, so
    # a fresh copy has to make them (a download into a missing mods/ fails).
    for d in (MODS_DIR, os.path.join(HERE, 'dump')):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as exc:
            print('could not create %s: %s' % (d, exc), file=sys.stderr)
    # BUILD 756: drop the extractions of mods that are no longer installed
    # as soon as the program starts, not only at the end of a build.
    try:
        import ff7nx_cachegc
        ff7nx_cachegc.sweep_orphans(MODS_DIR, print)
    except Exception as exc:                                    # noqa: BLE001
        print('cache cleanup skipped: %s' % exc, file=sys.stderr)
    if '--cli' in sys.argv:
        saved = build.load_settings(SETTINGS)
        # BUILD 804: the same values as the GUI.
        saved['__global__'] = nxsettings.effective_global(
            dict(default_globals(), **saved.get('__global__', {})))
        mods = discover_mods(*manual_moves(saved))
        migrate_saved_mod_keys(saved, mods)
        seed_mod_defaults(saved, mods)
        if build.FIELD_TEX_CAP_ENV not in os.environ:
            cap_value = saved.get('__global__', {}).get('field_tex_cap', 0)
            os.environ[build.FIELD_TEX_CAP_ENV] = str(cap_value)
        if build.WORLD_TEX_CAP_ENV not in os.environ:
            world_cap_value = saved.get('__global__', {}).get(
                'world_tex_cap', build.WORLD_TEX_CAP_DEFAULT)
            os.environ[build.WORLD_TEX_CAP_ENV] = str(world_cap_value)
        if build.BATTLE_BG_TEX_CAP_ENV not in os.environ:
            bg_cap_value = saved.get('__global__', {}).get(
                'battle_bg_tex_cap', 256)
            os.environ[build.BATTLE_BG_TEX_CAP_ENV] = str(bg_cap_value)
        if build.ff7nx_spelltex.FX_CAP_ENV not in os.environ:
            os.environ[build.ff7nx_spelltex.FX_CAP_ENV] = str(
                saved.get('__global__', {}).get('spell_fx_cap', 0))
        if build.ff7nx_spelltex.CAP_ENV not in os.environ:
            os.environ[build.ff7nx_spelltex.CAP_ENV] = str(
                saved.get('__global__', {}).get('spell_tex_cap', 512))
        if build.ff7nx_minigametex.CAP_ENV not in os.environ:
            os.environ[build.ff7nx_minigametex.CAP_ENV] = str(
                saved.get('__global__', {}).get(
                    'minigame_tex_cap', build.ff7nx_minigametex.DEFAULT_CAP))
        if build.ff7nx_fieldbg.PAGE_PX_ENV not in os.environ:
            _g = saved.get('__global__', {})
            _px = _g.get('field_bg_page_px', build.ff7nx_fieldbg.OFF_PAGE_PX)
            # Same migration as the dialog: before the ladder was split, 256
            # WAS "off". Reading it back literally would switch the repack on
            # for a headless build that had it off.
            if _px == 256 and not _g.get('field_bg_ladder_v2'):
                _px = build.ff7nx_fieldbg.OFF_PAGE_PX
            os.environ[build.ff7nx_fieldbg.PAGE_PX_ENV] = str(_px)
        # The headless half of the depth-1 wiring; see the note in `start()`.
        if build.ff7nx_fieldbg.D1_PX_ENV not in os.environ:
            os.environ[build.ff7nx_fieldbg.D1_PX_ENV] = str(
                saved.get('__global__', {}).get(
                    'field_bg_d1_px', build.ff7nx_fieldbg.VANILLA_D1_PX))
        if build.field_bg_shadow.ENV not in os.environ:
            os.environ[build.field_bg_shadow.ENV] = (
                '1' if saved.get('__global__', {}).get(
                    'field_bg_d1_art', True) else '0')
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
            _sg.get('field_bg_clamp_palettes', False))
        build.field_bg_dense.PRESERVE_CELL_COORDS = bool(
            _sg.get('field_bg_preserve_cell_coords', True))
        # FINDINGS-228/234. See the headless branch above -- `PROMOTE_FX_BASE`
        # is deliberately NOT set here.
        build.field_bg_dense.MULTIPAL_RECOLOUR = bool(
            _sg.get('field_bg_multipal', True))
        _fxb = _sg.get('field_bg_fx_base_fields', '')
        build.field_bg_dense.PROMOTE_FX_BASE_FIELDS = frozenset(
            s.strip().lower() for s in str(_fxb or '').split(',') if s.strip())
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
        os.environ.setdefault('SEVENTH_NX_SHADOWS', str(int(bool(
            saved.get('__global__', {}).get('model_shadows', 1)))))
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
        if _HEADLESS_GFX_ENV not in os.environ:
            _gp = saved.get('__global__', {}).get('gfx_pool_mb', None)
            if _gp is None:
                import ff7nx_gfxpool as _gfxpool_h
                _gp = _gfxpool_h.DEFAULT_MB
            os.environ[_HEADLESS_GFX_ENV] = str(_gp)
        if build.ff7nx_fieldbuf.SCALE_ENV not in os.environ:
            _fb = saved.get('__global__', {}).get(
                'field_buffer', build.ff7nx_fieldbuf.DEFAULT_SCALE)
            _fb = build.ff7nx_fieldbuf._norm(_fb)      # BUILD 757: 4.5
            if _fb is None:
                _fb = build.ff7nx_fieldbuf.DEFAULT_SCALE
            os.environ[build.ff7nx_fieldbuf.SCALE_ENV] = str(_fb)
        if build.voice_ogg.NORMALIZE_ENV not in os.environ:
            os.environ[build.voice_ogg.NORMALIZE_ENV] = str(
                saved.get('__global__', {}).get(
                    'voice_normalize', build.voice_ogg.NORMALIZE_DEFAULT))
        if build.ff7nx_shaders.SCALER_ENV not in os.environ:
            os.environ[build.ff7nx_shaders.SCALER_ENV] = str(
                saved.get('__global__', {}).get('scaler', '') or '')
        if build.ff7nx_shaders.BLACK_POINT_ENV not in os.environ:
            os.environ[build.ff7nx_shaders.BLACK_POINT_ENV] = str(
                saved.get('__global__', {}).get(
                    'hd_black_point',
                    build.ff7nx_shaders.BLACK_POINT_DEFAULT))
        if build.ff7nx_shaders.FXAA_ENV not in os.environ:
            os.environ[build.ff7nx_shaders.FXAA_ENV] = str(
                saved.get('__global__', {}).get('fxaa', '') or '')
        if build.ff7nx_shaders.VIDEO_ENV not in os.environ:
            os.environ[build.ff7nx_shaders.VIDEO_ENV] = str(
                saved.get('__global__', {}).get('video_shader', '') or '')
        _dr = saved.get('__global__', {}).get(           # BUILD 761/762
            'dock_resolution', DOCK_RES_DEFAULT)
        _dr = DOCK_RES_DEFAULT if _dr is None else str(_dr)
        if 'SEVENTH_NX_TITLE_SCREEN' not in os.environ:           # BUILD 763b
            os.environ['SEVENTH_NX_TITLE_SCREEN'] = str(
                saved.get('__global__', {}).get('title_screen',
                                                TITLE_SCREEN_DEFAULT))
        if _dr and 'SEVENTH_NX_DOCK_RES' not in os.environ:
            os.environ['SEVENTH_NX_DOCK_RES'] = _dr
        _esc = saved.get('__global__', {}).get('esui_texscale', 2)  # BUILD 760
        if _esc != 2 and ff7nx_esui.SCALE_ENV not in os.environ:
            os.environ[ff7nx_esui.SCALE_ENV] = str(_esc)
        if build.MOVIE_30FPS_ENV not in os.environ:
            os.environ[build.MOVIE_30FPS_ENV] = \
                '1' if saved.get('__global__', {}).get('movie_30fps') else '0'
        os.environ[build.ANALOG_360_ENV] = (
                '1' if saved.get('__global__', {}).get('analog_360') else '0')
        os.environ[build.NO_AUTORUN_ENV] = (
                '1' if saved.get('__global__', {}).get('no_autorun') else '0')
        if _HEADLESS_TEXCACHE_ENV not in os.environ:
            import ff7nx_texcache as _tc_h
            _g = saved.get('__global__', {})
            _tc = str(_g.get('texture_cache', '') or '')
            if _tc not in TEX_CACHE_LABELS:
                _tc = ('nocache' if _g.get('no_texture_cache')
                       else _tc_h.MODE)
            os.environ[_HEADLESS_TEXCACHE_ENV] = _tc
        os.environ[build.NO_CHEATS_ENV] = (
                '1' if saved.get('__global__', {}).get('no_cheats') else '0')
        os.environ.setdefault(build.WITEM_FIX_ENV, (
                '1' if saved.get('__global__', {}).get('witem_fix', True)
                else '0'))
        os.environ.setdefault(build.ADV_FACIAL_ENV, (
                '1' if saved.get('__global__', {}).get('advanced_facial', True)
                else '0'))
        os.environ.setdefault(build.TEXT_AUTOSIZE_ENV, (
                '1' if saved.get('__global__', {}).get('text_autosize', False)
                else '0'))
        os.environ.setdefault('SEVENTH_NX_COMPACT_CACHE', (
                '1' if saved.get('__global__', {}).get('compact_cache')
                else '0'))
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
        enabled = {m.filename: saved_enabled(saved, m) for m in mods}
        settings = {}
        for m in mods:
            m.ensure_extracted(print)
            settings[m.filename] = _mod_options_from_saved(m, saved)
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
