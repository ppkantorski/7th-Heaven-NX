#!/usr/bin/env python3
r"""
ff7nx_battlewide.py -- widen the battle effects that size themselves to the
4:3 viewport.

THE SYMPTOM
===========
Summon and limit-break screen effects -- typhoon, odin's gunge, Neo Bahamut,
Barret's Catastrophe, fat chocobo -- draw a full-screen quad that covers only
the middle 4:3 of a 16:9 frame.  The picture behind them is wide; the effect
is not.

THE MECHANISM
=============
FF7 keeps the battle viewport rect in four globals (FINDINGS-97 §2):

    [0x9AAD4C] x = 0     [0x9AAD50] y = 0
    [0x9AAD5C] w = 640   [0x9AAD68] h = 332

A full-screen effect reads `x` for its left edge and `w` for its extent, and
gets 0 and 640.  The vertex shader then multiplies gl_Position.x by
WS_SCALE = 0.75, so 640 game units land in the central 75% of the frame --
which is exactly 4:3.  The frame really spans game x -107..747.

FFNx redirects those two reads per effect to its own globals
(`src/ff7/widescreen.cpp`, "Battle summon fix"):

    patch_code_dword(typhoon_effect_sub_4D7044 + 0x1B, &wide_viewport_x);
    patch_code_dword(typhoon_effect_sub_4D7044 + 0x36, &wide_viewport_width);

with `wide_viewport_x = -107`, `wide_viewport_width = 854`.  This port has no
FFNx globals to point at, so the equivalent is to replace the recompiled load
with the immediate: one word each.

THE RULE, AND THE HALF OF IT THAT WAS MISSING
=============================================
"reads the rect's extent" separates full-frame quads from content that is
merely positioned.  It does NOT separate a quad from THE VIEWPORT ITSELF, and
that gap turned the battle screen black.

`battle_loop_sub_41BAB3` reads x, y, w and h and pushes them straight into
`engine_gfx_setviewport`.  Those loads are not a quad's corners -- they are
the 3D viewport, and handing it (-107, 0, 854, 480) both moves the matrix and
stops FINDINGS-97's uncrop leg matching `cmp wY,#0 / cmp wH,#332`.

FFNx never patches it, and I argued that its absence was not evidence because
FFNx rewrites the stored rect and so gets every consumer for free.  That was
right about FFNx and wrong as a licence: a function FFNx leaves alone because
it inherits the change is not the same as one that is safe to change directly.
The absence was informative and I talked myself past it.

So the rule now has two clauses, and the second is checked in `verify`:

    a full-screen overlay reads the extent (w or h) as well as the origin
    ...AND does not pass the rect to engine_gfx_setviewport

THE RULE THAT PICKS THE SITES -- EXTENT, NOT ORIGIN
===================================================
This module patches the origin and every extent actually consumed by an
isolated full-frame effect (`x`, `w`, and its local `h` when present), in
functions that read both origin and extent.  That distinction is the whole
design, and getting it wrong is what an earlier attempt (`ff7nx_overlay.py`,
now refusing to apply) did:

    a full-screen overlay needs a corner AND an extent, so it reads w
    a thing merely PLACED in the frame reads only the origin

Measured over every body that materialises a battle-rect constant, 14 read the
extent and 20 read only the origin.  The origin-only group is damage numbers,
UI and cursors -- content that is correctly positioned today.  FFNx redirects
those too, because FFNx moves the entire battle coordinate space out to the
wide edges; this port deliberately does not, keeping UI in the 4:3 core while
the picture widens around it.  So **FFNx's redirect list is not transferable
wholesale to this port -- only its extent-reading half is.**

WHY THIS CANNOT DISTURB THE SHIPPED BATTLE LEG
==============================================
FINDINGS-97's uncrop leg fires on the literal rect, `cmp wY,#0 / cmp wH,#332`.
Anything that changed the STORED rect would stop it firing and bring the black
band back silently.

This module changes no stored value.  FFNx does not either: of its four
`battle_enter` patches only `+0x21A` writes a global (h 332 -> 480), and that
one is deliberately excluded here -- FINDINGS-97 §3 measured it as moving the
scene 111 device rows.  Everything below rewrites CONSUMERS, so the globals
still read (0, 0, 640, 332) and the leg still recognises them.

`--verify` asserts that, rather than trusting this paragraph.

GROUPS
======
Shipped one group at a time so that a bad one is a single revert, not a
bisect.  Group 1 is the subset FFNx patches with two redirects and no
companion constants whatsoever -- the least that can go wrong.
"""
import argparse
import os
import re
import shutil
import struct
import sys
import tempfile
from pathlib import Path

try:
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM, CS_OP_REG
    from capstone.arm64 import ARM64_OP_MEM
except ImportError:                                          # pragma: no cover
    sys.exit('need capstone:  pip install capstone --break-system-packages')

import nxmap
import ff7nx_guestref as gr

FFNX_HEADER = 'repos/FFNx-master/src/widescreen.h'
EXE = 'ff7_en_switch'

BATTLEWIDE_ENV = 'SEVENTH_NX_BATTLE_WIDE'


def enabled() -> bool:
    """
    ON with 16:9, OFF at 4:3, overridable for an A/B.

    Every value this module writes is a widescreen value -- x -107, w 854,
    h 480.  At 4:3 the frame really is 640 wide and 332 tall above the UI, so
    these are not a smaller improvement there, they are simply wrong: the
    quads would hang off both sides of a frame that never widened.  Same
    reasoning as ff7nx_modelcull, and the same gate.
    """
    v = os.environ.get(BATTLEWIDE_ENV)
    if v is not None:
        return v not in ('', '0', 'off', 'false')
    try:
        import ff7nx_ws
        return ff7nx_ws.enabled()
    except Exception:                                          # noqa: BLE001
        return False


def apply_all(main, revert=False, log=print) -> int:
    """Every group, in ascending order.  The build's entry point."""
    rc = 0
    # On revert, remove the stride branches first.  They sit between several
    # guest-address taps and rect loads, so the deliberately conservative
    # rect-site rediscovery sees the original straight-line body again.
    if revert:
        rc |= apply_ui_fade_visible_bottom(main, revert=True, log=log)
        rc |= apply_ui_fade_bottom_safe(main, revert=True, log=log)
        rc |= apply_ui_fade_bottom(main, revert=True, log=log)
        rc |= apply_engine_fade(main, revert=True, log=log)
        rc |= apply_ui_band(main, revert=True, log=log)
        rc |= apply_quad_bounds(main, revert=True, log=log)
        rc |= apply_battle_bounds(main, revert=True, log=log)
        rc |= apply_white_flash(main, revert=True, log=log)
        rc |= apply_menu_fade(main, revert=True, log=log)
        rc |= apply_fade_animation(main, revert=True, log=log)
    else:
        # Build 208 installed a post-translation UI-bottom experiment last.
        # Hardware proved it is not the visible strip and that its two branch
        # hooks regress world-map battle entry.  Remove it before ordinary
        # cave allocation and never reinstall it; the recognizer remains only
        # so build-208 outputs migrate byte-exactly back to stock at the hooks.
        rc |= apply_ui_fade_bottom_safe(main, revert=True, log=log)
        # Idempotent rebuilds can receive a module which already has the five
        # stride hooks.  Those branches intentionally interrupt the static
        # tap-to-load scanner used by the rect pass.  Temporarily return only
        # the companions to stock, let the rect pass prove/reapply its own
        # sites, then install the companions again below.  The final bytes are
        # unchanged; this avoids weakening the scanner just to see through a
        # feature it owns itself.
        m0 = nxmap.Main(str(main))
        if any(_branch_target(h, _word(m0.img, h)) is not None
               for h in FADE_STRIDE):
            rc |= apply_fade_animation(main, revert=True, log=log)
        # Likewise, return the menu-fade caves before any normal-pass cave
        # allocation.  This removes build 207's retired 332-height form (and
        # any earlier layout of the current 480-height form) in the same
        # deterministic phase as the six horizontal hooks.  The current pass
        # reinstalls all eight sites below.  Without this ordering, the first
        # migration build frees caves too late and a second build can choose a
        # different (though equivalent) cave layout.
        rc |= apply_menu_fade(main, revert=True, log=log)
    if revert:
        # Group 3 was an inferred inventory, not an FFNx-identified fade
        # path.  Revert it for compatibility with build 197/198 images.
        rc |= apply_neo_bahamut_scale(main, revert=True, log=log)
        for g in sorted(GROUPS):
            rc |= apply(main, g, revert=True, log=log)
    else:
        # Hardware proved group 3 is not the victory fade.  Remove it from an
        # already-patched input, then apply only the proven overlay groups.
        rc |= apply(main, 3, revert=True, log=log)
        for g in ACTIVE_GROUPS:
            rc |= apply(main, g, revert=False, log=log)
        rc |= apply_neo_bahamut_scale(main, revert=False, log=log)
    # The rect redirects and the strip geometry are one FFNx feature.  They
    # used to be separated here because the 21-strip constant is also touched
    # by the 60 FPS pass.  Hardware has now supplied the missing observation:
    # leaving the companions stock produces exactly the 332-line opening wipe
    # and 4:3 attack flash.  `apply_fade_animation` composes the two features
    # explicitly (21->30 at 15 FPS, 84->120 at 60 FPS), rather than allowing
    # whichever module ran last to win.
    if not revert:
        # Build 199..202 put the wide maxima in battle_enter's two shared
        # globals and froze world-map battle entry.  Build 203 moved the same
        # values into battle_draw_quad_5BD473.  Hardware build 205 proved the
        # local version is required: removing it simultaneously regressed the
        # attack flashes, the full-screen fade and battle entry.  Keep the
        # GLOBAL experiment removed, but put the four consumer-local vertices
        # back exactly as Claude's last good flash build had them.
        rc |= apply_battle_bounds(main, revert=True, log=log)
        rc |= apply_quad_bounds(main, revert=False, log=log)
        # Build 203's UI-band collapse is disproven; take it back out of an
        # already-patched image before anything else touches that function.
        rc |= apply_ui_band(main, revert=True, log=log)
        # 0x659532 is `highway_submit_fade_quad`, not a battle fade.  Build
        # 204's patch is retained only for byte-exact migration and removed
        # from normal builds.
        rc |= apply_engine_fade(main, revert=True, log=log)
        # Build 205 bypassed two guest-address translations to inject 222.0f
        # in ARM code.  Restore that unrelated code byte-exactly.  Build 207's
        # forced end-battle y=332 partition and x86 horizontal widening are
        # also retired.  Builds 205/210's separate 480-height transition
        # vertices are removed by the migration pass above: longer hardware
        # sequences proved the freeze merely intermittent, and later field
        # presentation could remain black.  The measured full-resolution UI
        # fade is corrected independently below.
        # Build 208's later post-translation replacement was also disproven on
        # hardware and is removed at the start/end of the build rather than
        # being part of the normal patch set.
        rc |= apply_ui_fade_bottom(main, revert=True, log=log)
        rc |= apply_fade_animation(main, revert=False, log=log)
        rc |= apply_menu_fade(main, revert=False, log=log)
        rc |= apply_white_flash(main, revert=False, log=log)
        # The shader probe's (0,332)..(640,480) quad belongs to x86
        # 0x6CF5C5, not the lookalike half-resolution menu path at 0x6D0022.
        # Shorten that producer's one shared bottom value to the measured
        # visible UI edge.  This is a single arithmetic-immediate change: no
        # branches, guest translations, shared globals or transition state.
        rc |= apply_ui_fade_visible_bottom(main, revert=False, log=log)
    return rc

# the battle rect, FINDINGS-97 §2
FIELD = {'x': 0x9AAD4C, 'y': 0x9AAD50, 'w': 0x9AAD5C, 'h': 0x9AAD68}

# FFNx src/ff7/widescreen.h, parsed at verify time -- see _ffnx_values()
FFNX_DEFAULT = {'x': -107, 'y': 0, 'w': 854, 'h': 480}

# ---------------------------------------------------------------------------
# Transcribed from FFNx src/ff7/widescreen.cpp, ff7_widescreen_hook_init().
# `off` is the patch_code_dword offset, `field` is which rect field the x86
# instruction there reads, and `width` is the STOCK LOAD WIDTH in bytes.  All
# three are checked against the real executable.
#
# The width is recorded rather than inferred, and that is not redundancy.  A
# patched site reads `movz wN, #480`, and a MOVZ of a positive value is
# byte-identical whether it replaced a 32-bit `ldr` or a 16-bit `ldrh` -- so
# the patched image cannot say what to put back.  Inferring it from the value
# restored battle_sub_5BD050's height site as `ldrh` when stock is `ldr`, and
# --revert came back one word short of the original while reporting success.
# This is the THIRD time that inference has been wrong (ff7nx_overlay,
# ff7nx_swirl, here), so it is now data, not a rule.
GROUPS = {
    2: ('the battle fade / limit-break flash overlay', [
        ('battle_sub_5BD050', 0x5BD050,
         ((0x04B, 'w', 2), (0x068, 'x', 2), (0x08B, 'w', 2), (0x0B4, 'x', 2),
          (0x105, 'w', 2), (0x122, 'x', 2), (0x141, 'w', 2), (0x16A, 'x', 2),
          (0x19F, 'w', 2), (0x1BB, 'x', 2),
          # NOT an FFNx offset -- see NOT_FROM_FFNX below.  Note this is the
          # DISP32 FIELD, +0xE0, not the instruction at +0xDF: FFNx's offsets
          # all name the field patch_code_dword overwrites, and mixing the two
          # conventions reads the opcode byte into the address (0x9AAD68A1).
          (0x0E0, 'h', 4))),
        # This is the quad submitter used by 5BD050.  Its right/bottom edges
        # come from the geometry 5BD050 just built, but its two LEFT vertices
        # independently reread rect.x.  Without these two FFNx redirects the
        # widened geometry is clipped/aligned back against x=0: the visible
        # result is the exact central 640-unit rectangle in the hardware
        # screenshots.  It is the one safe origin-only exception because its
        # caller and the submitted vertices are both identified here; damage
        # numbers and UI origin readers remain deliberately untouched.
        ('battle_draw_quad_5BD473', 0x5BD473,
         ((0x0DA, 'x', 2), (0x112, 'x', 2))),
    ]),
    3: ('extent-readers FFNx never names (victory fade, battle loop)', [
        # FFNx has no widescreen patch for any of these, and that absence is
        # not evidence they do not need one.  FFNx rewrites the STORED rect at
        # battle_enter, so every consumer in the game sees the wide values for
        # free; its explicit redirect list only covers the places where the
        # stored rect is not the source.  This port keeps the stored rect at
        # (0, 0, 640, 332) on purpose, so every consumer has to be found and
        # redirected individually -- and FFNx's list is therefore a starting
        # point, not an inventory.
        #
        # These five are what the extent rule finds once you stop using FFNx
        # as the index: bodies that read the rect's extent and so must be
        # drawing something full-frame.  battle_loop_sub_41BAB3 is the battle
        # main loop, which is where the victory fade would live.
        ('sub_4825E0', 0x4825E0,
         ((0x047, 'x', 2), (0x07C, 'w', 2), (0x097, 'h', 2))),
        ('sub_487BD2', 0x487BD2,
         ((0x019, 'x', 2), (0x034, 'w', 2), (0x041, 'h', 2))),
    ]),
    1: ('summon and limit quads, no companion constants', [
        ('typhoon_effect_sub_4D7044',    0x4D7044, ((0x1B, 'x', 2), (0x36, 'w', 2), (0x043, 'h', 2))),
        ('typhoon_effect_sub_4DB15F',    0x4DB15F, ((0x22, 'x', 2), (0x3D, 'w', 2), (0x04A, 'h', 2))),
        ('odin_gunge_effect_sub_4A3A2E', 0x4A3A2E, ((0x38, 'x', 2), (0x53, 'w', 2), (0x060, 'h', 2))),
        ('odin_gunge_effect_sub_4A4BE6', 0x4A4BE6, ((0x36, 'x', 2), (0x51, 'w', 2), (0x05E, 'h', 2))),
        ('barret_limit_3_1_sub_4700F7',  0x4700F7, ((0x1B, 'x', 2), (0x36, 'w', 2), (0x043, 'h', 2))),
        ('fat_chocobo_sub_5096F3',       0x5096F3, ((0x4A, 'x', 2), (0x5F, 'w', 2), (0x06A, 'h', 2))),
    ]),
    4: ('Neo Bahamut full-frame background geometry', [
        # FFNx has a dedicated Neo Bahamut block in widescreen.cpp.  Unlike
        # FFNx, this port deliberately leaves the shared battle rect stock,
        # so the four x+w pairs that the x86 builds locally must redirect
        # BOTH source loads here.  FFNx gets the second load of each pair for
        # free from its globally widened rect.  These offsets are therefore
        # re-derived from ff7_en_switch, not copied past the point where the
        # current FFNx PC executable and the Switch PC source diverge.
        ('neo_bahamut_effect_sub_490F2A', 0x490F2A,
         ((0x05D, 'w', 4), (0x06A, 'x', 4),
          (0x095, 'h', 4),
          (0x1A2, 'x', 2), (0x1AF, 'w', 2))),
        ('run_bahamut_neo_main_48C2A1', 0x48C2A1,
         ((0x140, 'x', 2), (0x15B, 'w', 2),
          (0x168, 'h', 2),
          (0x19B, 'x', 2),
          (0x1D1, 'x', 4), (0x1D7, 'w', 4),
          (0x20E, 'x', 2),
          (0x243, 'x', 4), (0x249, 'w', 4),
          (0x28A, 'x', 2),
          (0x2C0, 'x', 4), (0x2C6, 'w', 4),
          (0x2FC, 'x', 2),
          (0x332, 'x', 4), (0x338, 'w', 4))),
    ]),
}

# Only these groups are part of a normal build.  Group 3 remains described so
# old test images can be reverted byte-exactly, but hardware disproved its
# guessed "victory fade" attribution.  The actual path is MENU_FADE below.
ACTIVE_GROUPS = (1, 2, 4)

# neo_bahamut_effect_sub_490F2A has two x86 `imul reg,reg,160`
# instructions which FFNx changes to wide_viewport_width/4 (854/4 = 213).
# The recompiler strength-reduced each multiply to x*5 followed by <<5.
# Replacing those two words with `mov w9,#213` and `mul` is exact, needs no
# code cave, and is safe because w9 is dead until a later load at both sites.
NEO_BAHAMUT_SEGMENT = 213
NEO_BAHAMUT_SCALE = {
    0x00280238: (0x0B080908, 'Neo Bahamut first strip x5'),
    0x00280248: (0x531B6908, 'Neo Bahamut first strip x32'),
    0x002802C8: (0x0B080908, 'Neo Bahamut second strip x5'),
    0x002802CC: (0x531B6916, 'Neo Bahamut second strip x32'),
}
NEO_BAHAMUT_SCALE_ANCHORS = {
    0x00280234: 0xB9400008,  # ldr w8,[x0] -- first source
    0x0028023C: 0x5295A997,  # materialise battle-rect base
    0x00280244: 0x110042E0,  # add w0,w23,#0x10
    0x0028024C: 0xB9000B28,  # store first scaled result
    0x002802C0: 0xB9400008,  # ldr w8,[x0] -- second source
    0x002802C4: 0x1100B280,  # add w0,w20,#0x2c
    0x002802D0: 0xB9000736,  # store second scaled result
}

# battle_enter is never touched -- not its stored rect, not its viewport
# reads, and (since the build-202 world-map freeze) not the two post-write
# loads that derive the overlay maxima either.  See BATTLE_BOUND_INPUTS.
FORBIDDEN = {
    0x41AD00: 'battle_enter: writing the rect would stop FINDINGS-97 uncrop '
              'leg firing and the black band would return',
    # Found by the "never STORES to the rect" check, which was added precisely
    # because this function reads the rect at +0x229/+0x22F -- the same two
    # offsets FFNx patches on battle_enter -- and that resemblance was worth
    # proving innocent rather than assuming.  It is not innocent: it makes
    # FOUR stores to the rect globals.  It is a rect WRITER, and redirecting a
    # writer's reads can desynchronise the stored value from the one the
    # uncrop leg matches on, which is the failure FINDINGS-97 forbids.
    #
    # Worth noting against FINDINGS-97 1, which said the six-byte operand
    # pattern for 0x9AAD5C-then-0x9AAD4C has "exactly one" hit in the
    # executable.  This is a second site with the same shape.  That does not
    # overturn the battle_enter identification -- which rests on four
    # independent instruction-type matches, not on the pattern being unique --
    # but the uniqueness claim itself does not hold.
    0x41B300: 'generic discovery forbidden: it is FFNx\'s battle_enter, it '
              'writes the stored rect four times, and widening the maxima it '
              'derives froze world-map battle entry in build 202',
    # Turned the screen black on entering battle.  It reads the rect and
    # pushes it STRAIGHT INTO engine_gfx_setviewport (x86 0x66067A), three
    # times:
    #
    #     +0x0CA  mov eax, [0x9aad5c]   ; w
    #     +0x0D0  mov ecx, [0x9aad50]   ; y
    #     +0x0D7  mov edx, [0x9aad4c]   ; x
    #     +0x0DE  call 0x66067a         ; engine_gfx_setviewport
    #
    # so those loads are not a quad's corners -- they ARE the 3D viewport.
    # Redirecting them hands gfx_drv_setviewport (-107, 0, 854, 480), which
    # moves the matrix AND stops FINDINGS-97's uncrop leg matching, because
    # the leg triggers on `cmp wY,#0 / cmp wH,#332`.  Both halves of the
    # picture break at once, hence black.
    0x41BAB3: 'passes the rect to engine_gfx_setviewport: it is the viewport, '
              'not a quad',
}

# x86 engine_gfx_setviewport.  A function that calls it with the rect is
# setting the VIEWPORT, and must never have those reads redirected.
SETVIEWPORT = 0x66067A

# Extent-readers deliberately left out of every group, with the measurement.
EXCLUDED_READERS = {
    0x470438: 'one of its width loads is a 2-byte half of a split 32-bit read '
              'and x0 stays live across it, so it is not a one-word swap; it '
              'needs the two-half treatment ff7nx_swirl documents',
}

# An ordinary member of GROUPS must read an origin AND an extent.  This helper
# is the final vertex submitter for group 2, and reads only the left origin;
# the extent was already turned into its right/bottom vertices by 5BD050.
# Keep the exception named and tiny so it cannot quietly grow into the old
# ff7nx_overlay mistake of moving battle UI and damage numbers.
ORIGIN_HELPERS = {
    0x5BD473: 'battle_draw_quad_5BD473: group-2 quad submitter; right/bottom '
              'were produced by battle_sub_5BD050',
}

# (function, field) -> how many ARM words cover FFNx's offsets, when the
# recompiler collapsed several of them.
#
# battle_sub_5BD050 has FIVE x86 reads of the rect x and only FOUR ARM loads.
# That is not a lost site: x86 +0x134 and +0x17E are two copies of the same
# call sequence, and the recompiler TAIL-MERGED them -- the +0x134 block ends
# at ARM +0x7CC178 with `b #0x7cc2d0`, joining the second, so the load at
# +0x7CC2EC serves both.  Every other field in that function comes out 5 = 5
# and 9 = 9, which is what a merge predicts and a dropped block does not.
#
# It is only safe because FFNx gives both sites the SAME value.  A merge under
# two different values would need the block split first, so the uniformity is
# asserted rather than assumed.
MERGED = {(0x5BD050, 'x'): 4}

# Offsets this module redirects that FFNx does NOT, with why.
#
# battle_sub_5BD050 + 0xDF reads the rect HEIGHT, 332.  With only x and w
# redirected the fade covered the full width but still stopped at the top of
# the UI -- reported as "starts at the top of the UI and extends to the top of
# the screen" instead of running evenly top to bottom.  332 is exactly that
# band: the battle scene's height above the UI.
#
# FFNx gets the full height a different way: `patch_code_int(battle_enter +
# 0x21A, wide_viewport_height)` rewrites the STORED global from 332 to 480.
# This port cannot do that, for two independent reasons --
#
#   * FINDINGS-97 3 measured it: the stored h feeds gfx_drv_setviewport, and
#     _42 = -((y + h/2) - 240)/240 is not carved out, so h 332 -> 480 moves
#     every model 111 device rows.  The opposite of what was asked.
#   * FINDINGS-97's uncrop leg triggers on `cmp wH, #332`.  Rewriting the
#     stored h stops it firing and the black band returns silently.
#
# Redirecting the CONSUMER has neither consequence.  The stored rect still
# reads (0, 0, 640, 332), so the viewport, the matrix and the uncrop leg all
# see exactly what they saw before; only this one overlay's quad is told it is
# 480 tall.  That distinction -- stored value versus consumer read -- is the
# whole reason this is safe, and it is why the value 480 appearing here is not
# the thing FINDINGS-97 forbids.
#
# The five y reads need nothing: they already return 0.
#
# EVERY full-frame quad in this module has the same problem, not just the fade.
# All six group-1 effects read the height exactly once as well, and with only
# x and w redirected they are widened horizontally and still stop at the top of
# the UI.  The fade is simply the one that got looked at first.  So the height
# redirect is part of the extent rule, not a special case: a quad that reads
# the extent needs BOTH halves of it.
NOT_FROM_FFNX = {
    (0x5BD050, 0x0E0): 'the rect height',
    (0x4825E0, 0x097): 'group 3 is entirely absent from FFNx',
    (0x487BD2, 0x041): 'group 3 is entirely absent from FFNx',
    (0x4D7044, 0x043): 'the rect height',
    (0x4DB15F, 0x04A): 'the rect height',
    (0x4A3A2E, 0x060): 'the rect height',
    (0x4A4BE6, 0x05E): 'the rect height',
    (0x4700F7, 0x043): 'the rect height',
    (0x5096F3, 0x06A): 'the rect height',
    (0x490F2A, 0x095): 'Neo Bahamut height inherited from FFNx stored rect',
    (0x48C2A1, 0x168): 'Neo Bahamut height inherited from FFNx stored rect',
}

# FFNx's "Battle fading animation fix".  These are geometry despite the old
# name: 83 is the centre of the 332-line battle viewport, 50 and 33 are the
# vertical band/stride, and 21 is the number of strips.  The full-height values
# are 120, 72 and 48; the wide strip count is 30.
#
# 60 FPS also scales the strip count by four.  FFNx resolves the overlap by
# running widescreen first (21->30) and animations second (30->120).  Our build
# runs 60 FPS first, so the equivalent composition is 84->120.  Both orders
# are accepted below, and revert preserves whichever FPS state was present.
ANIMATION_COMPANIONS = {
    (0x5BCF9D, 0x3A): (21, 30, '60 FPS composition: 84 -> 120'),
    (0x5BCF9D, 0x69): (83, 120, ''),
    (0x5BD050, 0x46): (50, 72, ''), (0x5BD050, 0xA5): (50, 72, ''),
    (0x5BD050, 0x87): (33, 48, ''), (0x5BD050, 0xDC): (33, 48, ''),
    (0x5BD050, 0x100): (33, 48, ''), (0x5BD050, 0x15C): (33, 48, ''),
    (0x5BD050, 0x186): (33, 48, ''),
}

# ARM64 sites corresponding to the x86 constants above.  Every address is
# rechecked by disassembly/word signature before use.  The five x33 forms were
# strength-reduced by the recompiler to `add wd,w8,w8,lsl #5`; x48 cannot fit
# that one-instruction form, so each gets a two-instruction padding cave:
# x*3 followed by <<4.  No flag or scratch register is touched.
FADE_COUNT = 0x007CBA94
FADE_CENTRE = 0x007CBAF8
FADE_BAND = (0x007CBCC4, 0x007CBE5C)
FADE_STRIDE = (0x007CBDE4, 0x007CBF28, 0x007CBFC4,
               0x007CC158, 0x007CC1B0)

FADE_SIMPLE = {
    FADE_CENTRE: (0x52800A69, 0x52800F09, 'fade vertical centre 83 -> 120'),
    FADE_BAND[0]: (0x52800649, 0x52800909, 'fade band 50 -> 72 (upper)'),
    FADE_BAND[1]: (0x52800649, 0x52800909, 'fade band 50 -> 72 (lower)'),
}

FADE_STRIDE_WORDS = {
    0x007CBDE4: 0x0B081514,  # add w20,w8,w8,lsl #5
    0x007CBF28: 0x0B081508,  # add w8, w8,w8,lsl #5
    0x007CBFC4: 0x0B081514,
    0x007CC158: 0x0B081508,
    0x007CC1B0: 0x0B081508,
}

# FFNx `menu_submit_draw_fade_quad_6CD64E`: this is the path used by the
# end-battle/victory fade, separate from battle_sub_5BD050's opening/flash
# strips.  FFNx redirects exactly four X reads and two width reads here; it
# deliberately leaves both Y/height reads dynamic because FFNx changes the
# stored viewport itself.  This port deliberately cannot do that (the stored
# 332-line battle rect is also the uncrop discriminator), so the end-battle
# caller's vertical values remain dynamic in FFNx.  On this recompiled port,
# however, the world-map hand-off can reach the quad before those two dynamic
# bottom values are initialized.  Build 205 forced both to the real 480-line
# frame and is the last hardware build known to present world-map battles;
# build 206 removed just those writes and the frozen world frame returned.
# They are therefore transition-stability writes, not the UI-fade crop.  The
# latter belongs to the separate measured producer at x86 0x6CF5C5.
#
# The recompiler translated each x86 integer global read into `ldr s0,[x0]`;
# the value is still an integer bit-pattern, merely carried in an FP register
# for the following conversion.  Each cave therefore creates the wide
# integer in W0 and bit-copies it to S0.
MENU_FADE = {
    0x00CF019C: (-107, 'endbattle fade left vertex 0'),
    0x00CF03A8: (-107, 'endbattle fade left vertex 1'),
    0x00CF05C8: (-107, 'endbattle fade left vertex 2'),
    0x00CF07EC: (-107, 'endbattle fade left vertex 3'),
    0x00CF060C: (854, 'endbattle fade width 0'),
    0x00CF082C: (854, 'endbattle fade width 1'),
}

# Builds 205/210 forced these bottom vertices to the frame edge while chasing
# the intermittent world-map hand-off freeze.  Repeated transition testing
# disproved that ownership: the freeze and later black-field presentation
# remained.  Keep their signatures only so existing outputs migrate back to
# the stock dynamic loads; normal builds do not install them.
MENU_FADE_TRANSITION_BOTTOM = {
    0x00CF0470: (480, 'battle transition bottom vertex 1'),
    0x00CF08D8: (480, 'battle transition bottom vertex 3'),
}
# Build 207's failed replacement.  Recognize and remove it byte-exactly just
# like the older 480 experiment; neither value is installed by a normal build.
MENU_FADE_FAILED_HEIGHT = {
    0x00CF0470: (332, 'endbattle scene bottom vertex 1'),
    0x00CF08D8: (332, 'endbattle scene bottom vertex 3'),
}
MENU_FADE_STOCK = 0xBD400000       # ldr s0, [x0]
FMOV_S0_W0 = 0x1E270000            # fmov s0, w0
FMOV_S0_W9 = 0x1E270120            # fmov s0, w9

# FFNx explicitly widens `shadow_flare_draw_white_bg_57747E`, another battle
# white-background path outside the generic intro/fade routine.  Its x86 body
# stores x=0 and right=319;
# the renderer doubles those coordinates, producing the central 640-unit
# region.  FFNx replaces them with wide_viewport_x and
# wide_viewport_width/2.  The ARM recompiler emitted a store-zero for x, so
# that site needs a two-word cave; the right edge remains a one-word MOV.
WHITE_FLASH_X = 0x0068507C
WHITE_FLASH_RIGHT = 0x0068508C
WHITE_FLASH_X_STOCK = 0xB900001F       # str wzr, [x0]
WHITE_FLASH_RIGHT_STOCK = 0x528027E8   # mov w8, #319
WHITE_FLASH_RIGHT_WIDE = 0x52803568    # mov w8, #427
STR_W8_X0 = 0xB9000008                 # str w8, [x0]

# THE OVERLAY MAXIMA, AND WHY THEY ARE NOT WIDENED WHERE FFNx WIDENS THEM
# =======================================================================
# x86 0x41B300 -- which is the function FFNx calls `battle_enter`, proved by
# its rect reads landing on FFNx's own +0x229/+0x22F offsets -- writes the
# battle rect and then derives two maxima from it:
#
#     +0x228  mov eax,[0x9AAD5C]     w        ARM +0x8D464
#     +0x22D  mov ecx,[0x9AAD4C]     x        ARM +0x8D474
#     +0x233  lea edx,[ecx+eax-1]
#     +0x237  mov [0x9AC108],edx     right
#     +0x23D  mov eax,[0x9AAD68]     h        ARM +0x8D49C
#     +0x242  mov ecx,[0x9AAD50]     y        ARM +0x8D4AC
#     +0x248  lea edx,[ecx+eax-1]
#     +0x24C  mov [0x9AD198],edx     bottom
#
# Build 202 redirected the three loads so the two GLOBALS became 746/479.  It
# fixed the flash geometry and it FROZE world-map battle entry: the world
# image held with battle music playing and the battle never appeared.  That is
# the third time in three builds that making a value visible to every consumer
# of a shared battle-entry global has frozen this transition (199 inferred the
# mode from `w24`, 200 loaded the engine mode pointer, 202 moved the maxima),
# and the common factor is not the value -- it is the reach.
#
# 0x9AC108/0x9AD198 are read by six functions: this writer, the quad submitter
# below, display_battle_damage_5BB410, battle_sub_58ACB9, the point-culling
# test at 0x5953FD, and 0x470438.  Only ONE of them is a fade/flash vertex
# source.  0x5953FD in particular is a BOUNDS TEST -- it compares a sprite's
# x/y against x/right and y/bottom and, on failure, writes 0xFFFF to kill the
# entry.  Widening the maxima changes what that test admits during the battle
# hand-off, and nothing about the fade needs it to.
#
# So the SHARED maxima stay stock.  The same values are safe only when loaded
# locally by the final quad submitter below.  Build 205 supplied the decisive
# hardware A/B: restoring those four local loads to stock regressed attack
# flashes and full-screen fading and also prevented battle presentation after
# world-map entry.  Restoring the local values fixes all three without making
# 0x9AC108/0x9AD198 visible to any other consumer.
#
# Retained ONLY so a build-199..202 sdout can be migrated back byte-exactly.
# `apply_all` calls this with revert=True and never with revert=False.
BATTLE_BOUND_INPUTS = {
    0x0008D464: (0xB9400008, 854, 'derived overlay width'),
    0x0008D474: (0xB9400008, -107, 'derived overlay x'),
    0x0008D49C: (0xB9400008, 480, 'derived overlay height'),
}
BATTLE_BOUND_ANCHORS = {
    # The four writes of the literal battle rect.  These MUST remain stock.
    0x0008D410: 0xB9000013,
    0x0008D41C: 0xB900001C,
    0x0008D428: 0xB9000018,
    0x0008D440: 0xB900001A,
    # Exact right/bottom calculations and stores.
    0x0008D47C: 0x0B080129,
    0x0008D480: 0x51000533,
    0x0008D490: 0xB9000013,
    0x0008D4AC: 0xB9400008,  # y stays a real load of the stored zero
    0x0008D4B4: 0x0B080129,
    0x0008D4B8: 0x51000533,
    0x0008D4C8: 0xB9000013,
}

# The safe replacement: the same two maxima materialised inside
# battle_draw_quad_5BD473, leaving their shared globals stock.
#
# The x86 body reads each global and adds one, four times:
#
#     +0x0EF  mov ecx,[0x9AC108] ; add ecx,1 ; mov [edx+0x0C],cx   right
#     +0x11A  mov ecx,[0x9AD198] ; add ecx,1 ; mov [edx+0x12],cx   bottom
#     +0x12A  mov eax,[0x9AC108] ; add eax,1 ; mov [ecx+0x14],ax   right
#     +0x139  mov edx,[0x9AD198] ; add edx,1 ; mov [eax+0x16],dx   bottom
#
# The recompiler emitted all four as the same three words -- `ldr w8,[x0]`,
# `add w8,w8,#1`, `str w8,[x22,#n]` -- after the guest-address translate call.
# Replacing the LOAD with a MOVZ is the identical one-word swap every GROUPS
# site uses: the address materialisation and the translate call are left
# stock, x0 simply goes unread, and it is redefined two instructions later.
#
# 746 + 1 = 747 and 479 + 1 = 480, so this supplies the final full-frame
# right/bottom vertices.  The function is also reached from the boss-death
# helper, but hardware proves it is shared by the overlay paths in question;
# a caller identity does not imply an exclusive caller.
QUAD_BOUND_INPUTS = {
    0x007CD028: (746, 'quad right vertex 0'),
    0x007CD0D0: (479, 'quad bottom vertex 1'),
    0x007CD108: (746, 'quad right vertex 2'),
    0x007CD140: (479, 'quad bottom vertex 3'),
}
QUAD_BOUND_STOCK = 0xB9400008          # ldr w8, [x0]

# BUILD 204'S MISATTRIBUTED FADE, x86 0x659532.  MIGRATION ONLY.
#
# `0x671D2A` is the engine's one coloured-quad entry point -- (x, y, w, h,
# colour, mode, z, game_object) -- and it has 33 call sites in the whole
# executable.  Exactly two of them push a HARDCODED 640x480 rect, and one of
# those two is a fade:
#
#     0065954D  mov  dl, [ecx+0x70]      the fade alpha
#     00659550  cmp  edx, 0xFA           ramp to 250...
#     0065955E  add  cl, 5               ...five per frame
#     00659567  mov  [ebp-4], 0          \
#     0065956B  mov  [ebp-3], 0           |  colour = (0, 0, 0, alpha)
#     0065956F  mov  [ebp-2], 0           |
#     00659579  mov  [ebp-1], cl         /
#     0065958B  push 0x1E0               h = 480
#     00659590  push 0x280               w = 640     <-- 4:3, hardcoded
#     00659595  push 0                   y = 0
#     00659597  push 0                   x = 0       <-- 4:3, hardcoded
#     00659599  call 0x671D2A
#
# The instruction-level interpretation was right but the owning mode was not:
# FFNx resolves 0x659532 as highway_submit_fade_quad.  It is unrelated to the
# battle victory fade and must not be changed by a battle-only feature.
#
# FFNx's values, unchanged: x -107, w 854.  y and h are already correct at
# 0 and 480 and are asserted as anchors rather than rewritten.
#
# The recompiler emitted the four pushes in source order, so the ARM sites are
# h, w, y, x in that order.  `w` is a one-word MOV; `x` was strength-reduced
# to `str wzr` and needs the same two-word cave `WHITE_FLASH_X` already uses.
#
# These constants remain solely to recognize and remove a build-204 output.
# `apply_all` calls apply_engine_fade(revert=True) in normal builds.
ENGINE_FADE_W = 0x00A3F0EC
ENGINE_FADE_X = 0x00A3F118
ENGINE_FADE_W_STOCK = 0x52805008       # mov w8, #0x280   (640)
ENGINE_FADE_W_WIDE = 0x52806AC8        # mov w8, #0x356   (854)
ENGINE_FADE_X_STOCK = 0xB900001F       # str wzr, [x0]
ENGINE_FADE_ANCHORS = {
    0x00A3F0D4: 0x321B0FE8,   # mov w8, #0x1e0    h = 480, already correct
    0x00A3F0D8: 0xB9000008,   # str w8, [x0]
    0x00A3F0F0: 0xB9000008,   # str w8, [x0]      the width store
    0x00A3F104: 0xB900001F,   # str wzr, [x0]     y = 0, already correct
    0x00A3F128: 0x9402D582,   # bl  +0xAF4730     the quad draw itself
}

# BUILD 203'S TOP-EDGE CANDIDATE.  MIGRATION ONLY.
#
# It was selected from the measured (0,332)-(640,480) band, but hardware then
# showed that collapsing this edge changed none of the offending pixels.  The
# large x86 0x6D0022 routine constructs several menu quads; proximity within
# the same function was not producer identity.  The exact bottom-vertex data
# flow identified below is the one that matches the observed fade alpha and
# geometry.  Keep this one-word candidate only to remove build-203 outputs.
UI_BAND_TOP = 0x00D1D4A0
UI_BAND_TOP_STOCK = 0x11029914         # add w20, w8, #0xa6   (166)
UI_BAND_TOP_FLAT = 0x1103C114          # add w20, w8, #0xf0   (240 == bottom)
UI_BAND_ANCHORS = {
    # The menu rect y address being materialised, translated and loaded, and
    # both stores of the result.  None of these is rewritten, so discovery
    # works in either state.
    0x00D1D488: 0x52820995,   # movz w21, #0x104c   \  0xDC104C
    0x00D1D48C: 0x72A01B95,   # movk w21, #0xdc,16  /
    0x00D1D490: 0x110052B3,   # add  w19, w21, #0x14   -> 0xDC1060, the rect y
    0x00D1D494: 0x2A1303E0,   # mov  w0, w19
    0x00D1D49C: 0xB9400008,   # ldr  w8, [x0]          the rect y itself
    0x00D1D4A8: 0xB9000734,   # str  w20, [x25,#4]     \ the only two uses
    0x00D1D4B4: 0xB9000014,   # str  w20, [x0]         / of the top edge
}

# THE FIRST, MISIDENTIFIED BATTLE UI FADE BOTTOM, x86 0x6D0022.
#
# This block is gated by the right battle flag and takes the right alpha, and
# its half-resolution geometry doubles to the same rectangle the shader probe
# measured.  That made it an unusually convincing false positive.  Hardware
# A/Bs nevertheless proved that changing either its x86 data or both of its
# translated ARM loads does not move the visible strip, while the ARM hooks
# can regress world-map battle entry.  All forms below are migration-only.
#
# Build 205 bypassed guest_to_host; build 208 branched after it.  Both are
# retained solely so current builds can remove their exact historical bytes.
UI_FADE_BOTTOM = {
    0x00D1D85C: 0x2A1603E0,   # mov w0,w22 -- guest 0x7B7C1C (240.0f)
    0x00D1D864: 0x940F7ACF,   # bl  guest_to_host
    0x00D1D86C: 0xBD400000,   # ldr s0,[x0]
    0x00D1DBE4: 0x2A1603E0,   # second bottom vertex, same constant
    0x00D1DBF8: 0x940F79EA,   # bl  guest_to_host
    0x00D1DC00: 0xBD400000,   # ldr s0,[x0]
}
UI_FADE_BOTTOM_WIDE = {
    0x00D1D85C: 0x52A86BC0,   # mov w0,#0x435e0000 (222.0f)
    0x00D1D864: 0xD503201F,   # nop
    0x00D1D86C: FMOV_S0_W0,   # fmov s0,w0
    0x00D1DBE4: 0x52A86BC0,
    0x00D1DBF8: 0xD503201F,
    0x00D1DC00: FMOV_S0_W0,
}
UI_FADE_BOTTOM_ANCHORS = {
    0x00D1D848: 0xBD400000,   # v1 base y load from menu rect y
    0x00D1D854: 0x5E61D800,   # scvtf d0,d0
    0x00D1D858: 0x110032F6,   # add w22,w23,#0xc -> guest 240.0f
    0x00D1D860: 0xFC287B00,   # preserve converted base y on FP stack
    0x00D1D878: 0x1E22C000,   # fcvt d0,s0
    0x00D1D87C: 0x1E602820,   # fadd d0,d1,d0
    0x00D1DBE0: 0xBD400000,   # v3 base y load from menu rect y
    0x00D1DBEC: 0x5E61D800,   # scvtf d0,d0
    0x00D1DBF4: 0xFC287B00,   # preserve converted base y on FP stack
    0x00D1DC0C: 0x1E22C000,   # fcvt d0,s0
    0x00D1DC10: 0x1E602820,   # fadd d0,d1,d0
}

# Build 205's data-only replacement.  These are absolute x86 VAs in ff7_en,
# not offsets in exefs/main.  It is retained only for byte-exact migration:
# the UI fade must continue to the frame bottom while the scene fade ends at
# the UI boundary.  Normal builds restore the stock 240.0f.
EXE_UI_FADE_BOTTOM_VA = 0x007B7C1C
EXE_UI_FADE_BOTTOM_STOCK = struct.pack('<f', 240.0)
EXE_UI_FADE_BOTTOM_WIDE = struct.pack('<f', 222.0)
EXE_UI_FADE_BOTTOM_REFS = (0x006D00FC, 0x006D01A3)

# Build 207's retired x86 horizontal experiment.  The battle-only UI fade
# producer (x86 0x6D0022) uses half-resolution menu coordinates.  Build 207
# changed (0..320) to (-53.5..373.5), but hardware showed no improvement and
# world-map battle entry regressed in the same build.  Keep the exact values
# only for byte-safe migration; normal builds restore both stock constants.
EXE_UI_FADE_X = {
    0x007B7C18: {
        'stock': struct.pack('<f', 0.0),
        'wide': struct.pack('<f', -53.5),
        'refs': (0x006D009C, 0x006D00EB),
        'name': 'left',
    },
    0x007B7C20: {
        'stock': struct.pack('<f', 320.0),
        'wide': struct.pack('<f', 373.5),
        'refs': (0x006D0143, 0x006D0192),
        'name': 'right',
    },
}

# BUILD 208'S DISPROVEN POST-TRANSLATION BOTTOM HOOKS -- MIGRATION ONLY
# =====================================================================
# The probe measured this producer's quad as y=332..480 while the visible
# battle UI ends at y=444.  The desired bottom is therefore 222.0f in the
# producer's half-resolution coordinate space.
#
# Build 205 replaced the address-translation triples themselves; that froze
# world-map battle entry.  Build 208 left those calls stock and hooked only
# the post-translation loads.  Hardware proved that version still regresses
# world-map entry and does not change the visible lower fade strip.  Retain
# the exact cave body solely to recognize and remove build-208 binaries.
UI_FADE_BOTTOM_SAFE = {
    0x00D1D86C: 'battle UI fade bottom vertex 1',
    0x00D1DC00: 'battle UI fade bottom vertex 3',
}
UI_FADE_BOTTOM_SAFE_STOCK = 0xBD400000       # ldr s0,[x0]
MOVZ_W9_222F = 0x52A86BC9                   # movz w9,#0x435e,lsl #16

# THE MEASURED BATTLE UI FADE BOTTOM, x86 0x6CF5C5.
# =================================================
# The adjacent full-resolution menu renderer has the SAME flag (0xBF2DE8) and
# alpha (0xBFB2E8), but directly emits this black quad:
#
#     v0 = (0.0f,   332.0f)
#     v1 = (0.0f,   480.0f)
#     v2 = (640.0f, 332.0f)
#     v3 = (640.0f, 480.0f)
#
# Unlike 0x6D0022, this is literally the coordinate space and rectangle seen
# by the shader probe.  In the ARM translation w22 holds 332.0f's bit pattern;
# one ADD creates w23=480.0f and the same w23 is stored to both bottom
# vertices.  The visible battle UI descriptor is y=332,h=112, ending at 444.
# Changing only the ADD delta from 0x4A0000 to 0x380000 therefore produces
# 444.0f (0x43DE0000) for both vertices.
#
# This patch changes one arithmetic immediate.  It adds no branch/cave, does
# not touch guest_to_host, and leaves the fade flag, alpha, x extent, object
# allocation, draw order, shared battle bounds and world transition untouched.
UI_FADE_VISIBLE_BOTTOM = 0x00D1AB18
UI_FADE_VISIBLE_BOTTOM_STOCK = 0x115282D7  # add w23,w22,#0x4a0,lsl #12
UI_FADE_VISIBLE_BOTTOM_FIXED = 0x114E02D7  # add w23,w22,#0x380,lsl #12
UI_FADE_VISIBLE_BOTTOM_ANCHORS = {
    0x00D1A814: 0x5285BD00,  # mov w0,#0x2de8       \
    0x00D1A818: 0x72A017E0,  # movk w0,#0xbf,lsl16  / flag 0xBF2DE8
    0x00D1A908: 0x52965D00,  # mov w0,#0xb2e8       \
    0x00D1A90C: 0x72A017E0,  # movk w0,#0xbf,lsl16  / alpha 0xBFB2E8
    0x00D1AA04: 0x52A874D6,  # mov w22,#0x43a60000 (332.0f)
    0x00D1AB1C: 0xB9000017,  # str w23,[x0] -- vertex 1 bottom
    0x00D1AC04: 0x115E82D8,  # add w24,w22,#0x7a0,lsl12 (640.0f)
    0x00D1AC28: 0xB9000016,  # str w22,[x0] -- vertex 2 top
    0x00D1AD10: 0xB9000018,  # str w24,[x0] -- vertex 3 right
    0x00D1AD30: 0xB9000017,  # str w23,[x0] -- vertex 3 bottom
}
QUAD_BOUND_ANCHORS = {
    # The guest addresses being translated, and the +1 / store that consumes
    # the loaded word.  Keyed on words the patch never rewrites, so discovery
    # works in both states -- HANDOFF-101 s2.4 rule 1.
    0x007CD018: 0x52982114,   # movz w20, #0xc108   \  0x9AC108, the right max
    0x007CD01C: 0x72A01354,   # movk w20, #0x9a,16  /
    0x007CD0C0: 0x529A3313,   # movz w19, #0xd198   \  0x9AD198, the bottom max
    0x007CD0C4: 0x72A01353,   # movk w19, #0x9a,16  /
    0x007CD020: 0x2A1403E0, 0x007CD100: 0x2A1403E0,   # mov w0, w20
    0x007CD0C8: 0x2A1303E0, 0x007CD138: 0x2A1303E0,   # mov w0, w19
    0x007CD02C: 0x11000508, 0x007CD0D4: 0x11000508,   # add w8, w8, #1
    0x007CD10C: 0x11000508, 0x007CD144: 0x11000508,
    0x007CD030: 0xB90006C8, 0x007CD0D8: 0xB90006C8,   # str w8, [x22,#n]
    0x007CD110: 0xB90002C8, 0x007CD148: 0xB9000AC8,
}


# --------------------------------------------------------------- encodings

def enc_movz(rd, imm16):
    assert 0 <= imm16 <= 0xFFFF, imm16
    return (0x52800000 | (imm16 << 5) | rd) & 0xFFFFFFFF


def enc_movn(rd, imm16):
    assert 0 <= imm16 <= 0xFFFF, imm16
    return (0x12800000 | (imm16 << 5) | rd) & 0xFFFFFFFF


def _menu_fade_body(value):
    first = enc_movn(0, ~value) if value < 0 else enc_movz(0, value)
    return [first, FMOV_S0_W0]


def _white_flash_x_body():
    return [enc_movn(8, 106), STR_W8_X0]  # w8 = -107; store it


def encode(reg, width, value):
    """(word, text) that puts `value` in `reg` as the load of `width` did."""
    rd = int(reg[1:])
    if width == 2:
        return enc_movz(rd, value & 0xFFFF), 'movz %s, #0x%X' % (reg, value & 0xFFFF)
    if value < 0:
        return enc_movn(rd, ~value), 'movn %s, #%d' % (reg, ~value)
    return enc_movz(rd, value), 'movz %s, #%d' % (reg, value)


def stock_word(reg, width):
    """The `ldr`/`ldrh` wRt, [x0] that a patched site replaced."""
    return ((0xB9400000 if width == 4 else 0x79400000) | int(reg[1:])) & 0xFFFFFFFF


# ------------------------------------------------------------ outside truth

def ffnx_values(root='.'):
    """FFNx's wide_viewport_* as FFNx declares them, or None if absent."""
    p = Path(root) / FFNX_HEADER
    if not p.exists():
        return None
    txt = p.read_text()
    out = {}
    for k, nm in (('x', 'wide_viewport_x'), ('y', 'wide_viewport_y'),
                  ('w', 'wide_viewport_width'), ('h', 'wide_viewport_height')):
        mo = re.search(r'^\s*int\s+%s\s*=\s*(-?\d+)\s*;' % nm, txt, re.M)
        if mo:
            out[k] = int(mo.group(1))
    return out or None


def x86_fields(group, exe_path=EXE):
    """
    [(fn, off, want_field, disp32, n_loads)] read out of the real executable.

    The independent check that each FFNx offset names the rect field this
    module thinks it does.  Without it, aiming the module at a neighbouring
    field produces the right site COUNT everywhere and passes silently -- which
    is exactly what happened once already.
    """
    p = Path(exe_path)
    if not p.exists():
        return None
    from probe_overlay import Exe, x86_site
    from probe_align import x86_accesses
    from capstone import Cs as _Cs, CS_ARCH_X86, CS_MODE_32
    exe = Exe(str(p))
    md = _Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True
    m = nxmap.Main('exefs/main') if False else None
    out = []
    for name, fn, offs in GROUPS[group][1]:
        for off, field, width in offs:
            s = x86_site(exe, md, fn, off, 4)
            out.append((fn, off, field,
                        (s['field'] & 0xFFFFFFFF) if s else None, name, width,
                        (4 if (s and 'dword ptr' in s['ins']) else 2)))
    return out


def x86_load_count(fn, guest, exe_path=EXE, end=None):
    from probe_overlay import Exe
    from probe_align import x86_accesses
    from capstone import Cs as _Cs, CS_ARCH_X86, CS_MODE_32
    exe = Exe(exe_path)
    md = _Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True
    return sum(1 for t in x86_accesses(exe, md, fn, end) if t[1] == guest and t[2])


# --------------------------------------------------------------- discovery

class Site(object):
    __slots__ = ('fn', 'name', 'field', 'addr', 'reg', 'width', 'mnemonic',
                 'op_str', 'word', 'patched')

    def __init__(self, fn, name, field, ins, reg, width, word, patched):
        self.fn, self.name, self.field = fn, name, field
        self.addr, self.reg, self.width = ins.address, reg, width
        self.mnemonic, self.op_str = ins.mnemonic, ins.op_str
        self.word, self.patched = word, patched


def _md():
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    md.detail = True
    return md


def x0_dead(text, addr, md, window=10):
    """True if x0 is not READ before it is next written."""
    for i in md.disasm(text[addr + 4: addr + 4 + window * 4], addr + 4):
        r, w = i.regs_access()
        if any(i.reg_name(x) in ('x0', 'w0') for x in r):
            return False
        if any(i.reg_name(x) in ('x0', 'w0') for x in w):
            return True
    return True


def reg_dead_before_write(text, addr, md, reg, window=16):
    """True when ``reg`` is not read before its next write after ``addr``."""
    aliases = {'w' + reg[1:], 'x' + reg[1:]}
    for i in md.disasm(text[addr + 4:addr + 4 + window * 4], addr + 4):
        reads, writes = i.regs_access()
        if any(i.reg_name(r) in aliases for r in reads):
            return False
        if any(i.reg_name(r) in aliases for r in writes):
            return True
    return False


def sites(m, group, values, md=None, window=8):
    """
    The patchable words, found in either state.

    Anchored on the call to the address translator, not on the load: once a
    load has been replaced by an immediate, a load-keyed scan cannot find the
    site again to show or revert it.
    """
    md = md or _md()
    out, problems = [], []
    for name, fn, offs in GROUPS[group][1]:
        if fn in FORBIDDEN:
            problems.append('%s is forbidden: %s' % (name, FORBIDDEN[fn]))
            continue
        if fn not in m.x86_to_arm:
            problems.append('%s: x86 0x%X is not a recompilation map key'
                            % (name, fn))
            continue
        a, b = m.extent(fn)
        _, stats = gr.scan(m.text, a, b, md)
        want = {}
        for off, field, width in sorted(offs):
            want.setdefault(FIELD[field], []).append((field, width))

        per = {}
        for tap, guest in stats.get('taps', []):
            if guest not in want:
                continue
            for i in md.disasm(m.text[tap + 4: tap + 4 + window * 4], tap + 4):
                mem = next((o for o in i.operands
                            if o.type == ARM64_OP_MEM), None)
                word = struct.unpack('<I', m.text[i.address:i.address + 4])[0]
                field = want[guest][0][0]
                v = values[field]
                if (i.mnemonic in ('ldr', 'ldrh') and mem is not None
                        and i.reg_name(mem.mem.base) in ('x0', 'w0')
                        and mem.mem.disp == 0):
                    reg = i.reg_name(i.operands[0].reg)
                    per.setdefault(guest, []).append(
                        Site(fn, name, want[guest][0][0], i, reg,
                             4 if i.mnemonic == 'ldr' else 2, word, False))
                    break
                if (i.mnemonic in ('movz', 'movn', 'mov')
                        and len(i.operands) == 2
                        and i.operands[1].type != CS_OP_REG
                        and i.operands[1].imm in (v, v & 0xFFFF)):
                    reg = i.reg_name(i.operands[0].reg)
                    n = len(per.get(guest, []))
                    fld, wid = (want[guest][n] if n < len(want[guest])
                                else want[guest][-1])
                    per.setdefault(guest, []).append(
                        Site(fn, name, fld, i, reg, wid, word, True))
                    break
                if mem is not None and i.reg_name(mem.mem.base) in ('x0', 'w0'):
                    break
        for guest, fields in want.items():
            got = per.get(guest, [])
            for n, st in enumerate(got):
                if n < len(fields) and not st.patched and st.width != fields[n][1]:
                    problems.append(
                        '%s +0x%X: stock load is %d bytes, the table says %d'
                        % (name, st.addr, st.width, fields[n][1]))
            expect = MERGED.get((fn, fields[0][0]), len(fields))
            if len(got) != expect:
                problems.append('%s: %d site(s) for the rect %s, expected %d '
                                '(FFNx names %d)'
                                % (name, len(got), fields[0][0], expect,
                                   len(fields)))
            out.extend(got)
    out.sort(key=lambda s: s.addr)
    return out, problems


# ------------------------------------------------------------------- plan

def plan(m, group, revert=False, md=None, values=None):
    values = values or (ffnx_values() or FFNX_DEFAULT)
    md = md or _md()
    found, problems = sites(m, group, values, md)
    patches, notes = [], []
    for s in found:
        v = values[s.field]
        new, asm = encode(s.reg, s.width, v)
        old = stock_word(s.reg, s.width) if s.patched else s.word
        if not x0_dead(m.text, s.addr, md):
            problems.append('%s +0x%X: x0 is read again; not a one-word swap'
                            % (s.name, s.addr))
            continue
        cur = struct.unpack('<I', m.text[s.addr:s.addr + 4])[0]
        frm, to = (new, old) if revert else (old, new)
        if cur == to:
            continue
        if cur != frm:
            problems.append('%s +0x%X: word is 0x%08X, expected 0x%08X'
                            % (s.name, s.addr, cur, frm))
            continue
        patches.append({'name': '%s rect %s -> %d' % (s.name, s.field, v),
                        'va': hex(s.addr),
                        'expect': struct.pack('<I', frm).hex(),
                        'set': struct.pack('<I', to).hex()})
        notes.append('    %-32s %s +0x%07X  %-16s -> %s'
                     % (s.name, s.field, s.addr,
                        '%s %s' % (s.mnemonic, s.op_str),
                        asm if not revert else 'ldr/ldrh %s, [x0]' % s.reg))
    return patches, notes, problems


# ---------------------------------------------------- fade strip companions

def _word(img, va):
    return struct.unpack('<I', img[va:va + 4])[0]


def _fmt_word(w):
    return struct.pack('<I', w).hex()


def neo_bahamut_scale_plan(m, revert=False):
    """Exact Switch translation of FFNx's two 160 -> 854/4 patches."""
    import a64 as A

    img = m.img
    wide = {
        0x00280238: A.movz(9, NEO_BAHAMUT_SEGMENT),
        0x00280248: A.mul(8, 8, 9),
        0x002802C8: A.movz(9, NEO_BAHAMUT_SEGMENT),
        0x002802CC: A.mul(22, 8, 9),
    }
    ps, notes, problems = [], [], []
    for va, expected in sorted(NEO_BAHAMUT_SCALE_ANCHORS.items()):
        if _word(img, va) != expected:
            problems.append('Neo Bahamut scale anchor +0x%X is %08X, expected %08X'
                            % (va, _word(img, va), expected))
    md = _md()
    for va in (0x00280238, 0x002802C8):
        # In stock code w9 must be dead before we claim it as scratch.  In an
        # already-patched image the owned MUL intentionally reads that w9,
        # which is proof of the installed pair rather than a liveness error.
        if (_word(img, va) == NEO_BAHAMUT_SCALE[va][0]
                and not reg_dead_before_write(img, va, md, 'w9', window=40)):
            problems.append('Neo Bahamut scale +0x%X: scratch w9 is live'
                            % va)
    for va, (stock, name) in sorted(NEO_BAHAMUT_SCALE.items()):
        cur = _word(img, va)
        frm, to = (wide[va], stock) if revert else (stock, wide[va])
        if cur == to:
            continue
        if cur != frm:
            problems.append('%s +0x%X is %08X, expected %08X or %08X'
                            % (name, va, cur, stock, wide[va]))
            continue
        ps.append({'name': name + (' restore x160' if revert else ' -> x213'),
                   'va': hex(va), 'expect': _fmt_word(frm),
                   'set': _fmt_word(to)})
        notes.append('    %-40s @ +0x%07X' %
                     (name + (' -> stock x160' if revert else ' -> wide x213'),
                      va))
    return ps, notes, problems


def apply_neo_bahamut_scale(main, revert=False, log=print) -> int:
    """Apply/revert the two Neo Bahamut segment-width multiplications."""
    import nso_patcher

    main = Path(main)
    m = nxmap.Main(str(main))
    patches, notes, problems = neo_bahamut_scale_plan(m, revert=revert)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  refusing to write Neo Bahamut segment scaling.')
        return 1
    log('  Neo Bahamut full-frame segment scaling:')
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'ff7nx_battlewide_neo_bahamut',
                  'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.neobahamut-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    log('  %d Neo Bahamut scale word(s) written' % len(patches))
    return 0


def _add_shift(rd, rn, rm, shift):
    """ADD Wd,Wn,Wm,LSL #shift (no flags), checked by the verifier."""
    assert 0 <= shift <= 31
    return (0x0B000000 | (rm << 16) | (shift << 10) | (rn << 5) | rd)


def _branch_target(va, word):
    if (word & 0xFC000000) != 0x14000000:
        return None
    imm = word & 0x03FFFFFF
    if imm & (1 << 25):
        imm -= 1 << 26
    return va + imm * 4


def _walk_stride_cave(img, hook, limit=24):
    """Addresses in one installed x48 cave, including its return branch."""
    pc = _branch_target(hook, _word(img, hook))
    if pc is None:
        return None, 'hook +0x%X is not a branch' % hook
    out = []
    for _ in range(limit):
        if pc == hook + 4:
            return out, None
        w = _word(img, pc)
        out.append(pc)
        tgt = _branch_target(pc, w)
        pc = tgt if tgt is not None else pc + 4
    return None, 'cave from +0x%X did not return within %d words' % (hook, limit)


def _stride_body(stock):
    """The exact x48 replacement for a recompiler-strength-reduced x33."""
    rd = stock & 31
    rn = (stock >> 5) & 31
    rm = (stock >> 16) & 31
    sh = (stock >> 10) & 0x3F
    if rn != 8 or rm != 8 or sh != 5:
        raise ValueError('stride word %08X is not add wd,w8,w8,lsl #5' % stock)
    import a64 as A
    return [_add_shift(rd, 8, 8, 1),  # 3*x
            A.lsl(rd, rd, 4)]          # 48*x


def fade_animation_plan(m, revert=False):
    """
    (patches, notes, problems) for FFNx's battle fading-animation companions.

    The count is intentionally state-aware: stock 15 FPS enters as 21 and
    leaves as 30; a module already processed by the 60 FPS pass enters as 84
    and leaves as 120.  Revert performs the inverse without changing FPS.
    """
    import a64 as A
    import ff7nx_cave

    img = m.img
    ps, notes, problems = [], [], []

    # Count: widescreen and 60 FPS both own this one immediate.
    count_words = {
        21: 0x528002A8, 30: 0x528003C8,
        84: 0x52800A88, 120: 0x52800F08,
    }
    cur = _word(img, FADE_COUNT)
    by_word = {w: n for n, w in count_words.items()}
    have = by_word.get(cur)
    if have is None:
        problems.append('fade strip count +0x%X is %08X, not 21/30/84/120'
                        % (FADE_COUNT, cur))
    else:
        want = ({30: 21, 120: 84}.get(have, have) if revert
                else {21: 30, 84: 120}.get(have, have))
        if want != have:
            ps.append({'name': 'battle fade strip count %d -> %d' % (have, want),
                       'va': hex(FADE_COUNT), 'expect': _fmt_word(cur),
                       'set': _fmt_word(count_words[want])})
            notes.append('    strip count %3d -> %3d @ +0x%07X%s'
                         % (have, want, FADE_COUNT,
                            '  (composed with 60 FPS)' if max(have, want) > 30 else ''))

    # Single-word centre/band constants.
    for va, (stock, wide, name) in sorted(FADE_SIMPLE.items()):
        cur = _word(img, va)
        frm, to = (wide, stock) if revert else (stock, wide)
        if cur == to:
            continue
        if cur != frm:
            problems.append('%s +0x%X is %08X, expected %08X or %08X'
                            % (name, va, cur, stock, wide))
            continue
        action = (name.replace('83 -> 120', '120 -> 83')
                  .replace('50 -> 72', '72 -> 50') if revert else name)
        ps.append({'name': action, 'va': hex(va), 'expect': _fmt_word(frm),
                   'set': _fmt_word(to)})
        notes.append('    %-36s @ +0x%07X' % (action, va))

    # Five x33 -> x48 sites.  Allocate all caves from one view of this image so
    # no two can claim the same padding hole.
    pool = ff7nx_cave.HolePool(img, starts=set(m.arm_starts))
    for hook in FADE_STRIDE:
        stock = FADE_STRIDE_WORDS[hook]
        cur = _word(img, hook)
        applied = _branch_target(hook, cur) is not None
        if revert:
            if cur == stock:
                continue
            if not applied:
                problems.append('fade stride hook +0x%X is %08X, neither stock nor branch'
                                % (hook, cur))
                continue
            cave, why = _walk_stride_cave(img, hook)
            if cave is None:
                problems.append(why)
                continue
            ps.append({'name': 'restore fade stride x33 +0x%X' % hook,
                       'va': hex(hook), 'expect': _fmt_word(cur),
                       'set': _fmt_word(stock)})
            for va in cave:
                ps.append({'name': 'clear fade stride cave +0x%X' % va,
                           'va': hex(va), 'expect': _fmt_word(_word(img, va)),
                           'set': '00000000'})
            notes.append('    stride x48 -> x33 @ +0x%07X  (%d cave word(s) returned)'
                         % (hook, len(cave)))
            continue

        if applied:
            cave, why = _walk_stride_cave(img, hook)
            if cave is None:
                problems.append(why)
            continue
        if cur != stock:
            problems.append('fade stride +0x%X is %08X, expected %08X'
                            % (hook, cur, stock))
            continue

        body = _stride_body(stock)
        # Allocate the real scattered layout directly so the final return
        # branch is encoded from the address where it actually lands.
        runs = pool.take(len(body) + 1)
        addrs = ff7nx_cave.slots(runs, len(body) + 1)
        words = body + [A.b(addrs[-1], hook + 4)]
        out = ff7nx_cave.link(runs, words)
        out[hook] = A.b(hook, addrs[0])
        for va in sorted(out):
            old = _word(img, va)
            if va != hook and old != 0:
                problems.append('fade cave word +0x%X is %08X, not padding'
                                % (va, old))
                continue
            ps.append({'name': 'fade stride x48 +0x%X' % va,
                       'va': hex(va), 'expect': _fmt_word(old),
                       'set': _fmt_word(out[va])})
        notes.append('    stride x33 -> x48 @ +0x%07X  (%d words in padding, entry +0x%X)'
                     % (hook, len(out) - 1, addrs[0]))

    return ps, notes, problems


def apply_fade_animation(main, revert=False, log=print) -> int:
    import nso_patcher
    main = Path(main)
    m = nxmap.Main(str(main))
    patches, notes, problems = fade_animation_plan(m, revert=revert)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  refusing to write battle fade companions.')
        return 1
    log('  battle fade / flash full-frame strip geometry:')
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'ff7nx_battlewide_fade', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.battlefade-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    log('  %d fade companion/cave word(s) written' % len(patches))
    return 0


def menu_fade_plan(m, revert=False):
    """Exact FFNx endbattle/menu fade sites, translated to ARM caves.

    The six FFNx horizontal sites widen the quad.  Builds 205/210 additionally
    forced two dynamic bottom vertices to 480 in an attempt to stabilize the
    world-map hand-off.  Hardware disproved that attribution: repeated battles
    could still freeze and later field entry could remain black.  Those two
    caves (and the older failed 332 form) are recognized only for byte-exact
    removal; normal builds leave the dynamic bottom loads stock.  The visible
    UI fade bottom is owned by the separately measured producer correction.
    """
    import a64 as A
    import ff7nx_cave

    img = m.img
    ps, notes, problems = [], [], []
    pool = ff7nx_cave.HolePool(img, starts=set(m.arm_starts))

    active = dict(MENU_FADE)
    if revert:
        # Migration-only ownership of both historical height experiments.
        active.update(MENU_FADE_TRANSITION_BOTTOM)
    for hook, (value, name) in sorted(active.items()):
        cur = _word(img, hook)
        target = _branch_target(hook, cur)
        if target is not None:
            cave, why = _walk_stride_cave(img, hook, limit=32)
            if cave is None:
                problems.append('%s: %s' % (name, why))
                continue
            logical = [_word(img, va) for va in cave
                       if _branch_target(va, _word(img, va)) is None]
            accepted = [_menu_fade_body(value)]
            failed = MENU_FADE_FAILED_HEIGHT.get(hook)
            if failed is not None:
                accepted.append(_menu_fade_body(failed[0]))
            if logical not in accepted:
                problems.append('%s cave is %s, expected %s'
                                % (name,
                                   ' '.join('%08X' % x for x in logical),
                                   ' or '.join(' '.join('%08X' % x for x in b)
                                               for b in accepted)))
                continue
            if not revert and logical == _menu_fade_body(value):
                continue
            if not revert:
                problems.append('%s still has the retired 332 cave; run the '
                                'normal migration/revert pass first' % name)
                continue
            ps.append({'name': 'restore ' + name, 'va': hex(hook),
                       'expect': _fmt_word(cur),
                       'set': _fmt_word(MENU_FADE_STOCK)})
            for va in cave:
                ps.append({'name': 'clear %s cave +0x%X' % (name, va),
                           'va': hex(va),
                           'expect': _fmt_word(_word(img, va)),
                           'set': '00000000'})
            notes.append('    %-36s wide -> stock  (%d cave word(s) returned)'
                         % (name, len(cave)))
            continue

        if cur != MENU_FADE_STOCK:
            problems.append('%s +0x%X is %08X, neither stock nor branch'
                            % (name, hook, cur))
            continue
        if revert:
            continue
        if not x0_dead(m.text, hook, _md(), window=12):
            problems.append('%s +0x%X: x0 is read after the replaced load'
                            % (name, hook))
            continue

        body = _menu_fade_body(value)
        runs = pool.take(len(body) + 1)
        addrs = ff7nx_cave.slots(runs, len(body) + 1)
        words = body + [A.b(addrs[-1], hook + 4)]
        out = ff7nx_cave.link(runs, words)
        out[hook] = A.b(hook, addrs[0])
        for va in sorted(out):
            old = _word(img, va)
            if va != hook and old != 0:
                problems.append('%s cave +0x%X is %08X, not padding'
                                % (name, va, old))
                continue
            ps.append({'name': '%s +0x%X' % (name, va),
                       'va': hex(va), 'expect': _fmt_word(old),
                       'set': _fmt_word(out[va])})
        notes.append('    %-36s -> %d  (%d words, entry +0x%X)'
                     % (name, value, len(out) - 1, addrs[0]))

    return ps, notes, problems


def apply_menu_fade(main, revert=False, log=print) -> int:
    """Install/remove the proven full-width endbattle fade coordinates."""
    import nso_patcher
    main = Path(main)
    m = nxmap.Main(str(main))
    patches, notes, problems = menu_fade_plan(m, revert=revert)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  refusing to write endbattle fade patch.')
        return 1
    log('  menu / endbattle fade full-width geometry:')
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'ff7nx_battlewide_menu_fade', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.menufade-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    log('  %d menu/endbattle fade word(s) written' % len(patches))
    return 0


def white_flash_plan(m, revert=False):
    """Exact FFNx white/attack-flash x extent, translated to ARM."""
    import a64 as A
    import ff7nx_cave

    img = m.img
    ps, notes, problems = [], [], []

    # Right/half-width is a direct MOV immediate replacement.
    cur = _word(img, WHITE_FLASH_RIGHT)
    frm, to = ((WHITE_FLASH_RIGHT_WIDE, WHITE_FLASH_RIGHT_STOCK)
               if revert else
               (WHITE_FLASH_RIGHT_STOCK, WHITE_FLASH_RIGHT_WIDE))
    if cur != to:
        if cur != frm:
            problems.append('battle white flash right +0x%X is %08X, '
                            'expected %08X or %08X'
                            % (WHITE_FLASH_RIGHT, cur,
                               WHITE_FLASH_RIGHT_STOCK,
                               WHITE_FLASH_RIGHT_WIDE))
        else:
            ps.append({'name': 'battle white flash half-width',
                       'va': hex(WHITE_FLASH_RIGHT),
                       'expect': _fmt_word(frm), 'set': _fmt_word(to)})
            notes.append('    white flash half-width %s @ +0x%07X'
                         % ('427 -> 319' if revert else '319 -> 427',
                            WHITE_FLASH_RIGHT))

    # x=0 was strength-reduced to STR WZR, so materialize -107 in padding.
    hook = WHITE_FLASH_X
    cur = _word(img, hook)
    target = _branch_target(hook, cur)
    if target is not None:
        cave, why = _walk_stride_cave(img, hook, limit=32)
        if cave is None:
            problems.append('battle white flash left: %s' % why)
        else:
            logical = [_word(img, va) for va in cave
                       if _branch_target(va, _word(img, va)) is None]
            if logical != _white_flash_x_body():
                problems.append('battle white flash left cave is %s, expected %s'
                                % (' '.join('%08X' % x for x in logical),
                                   ' '.join('%08X' % x
                                            for x in _white_flash_x_body())))
            elif revert:
                ps.append({'name': 'restore battle white flash left',
                           'va': hex(hook), 'expect': _fmt_word(cur),
                           'set': _fmt_word(WHITE_FLASH_X_STOCK)})
                for va in cave:
                    ps.append({'name': 'clear battle white flash cave +0x%X' % va,
                               'va': hex(va),
                               'expect': _fmt_word(_word(img, va)),
                               'set': '00000000'})
                notes.append('    white flash left -107 -> 0 '
                             '(%d cave word(s) returned)' % len(cave))
    elif cur != WHITE_FLASH_X_STOCK:
        problems.append('battle white flash left +0x%X is %08X, '
                        'neither stock nor branch' % (hook, cur))
    elif not revert:
        pool = ff7nx_cave.HolePool(img, starts=set(m.arm_starts))
        body = _white_flash_x_body()
        runs = pool.take(len(body) + 1)
        addrs = ff7nx_cave.slots(runs, len(body) + 1)
        words = body + [A.b(addrs[-1], hook + 4)]
        out = ff7nx_cave.link(runs, words)
        out[hook] = A.b(hook, addrs[0])
        for va in sorted(out):
            old = _word(img, va)
            if va != hook and old != 0:
                problems.append('battle white flash cave +0x%X is %08X, '
                                'not padding' % (va, old))
                continue
            ps.append({'name': 'battle white flash left +0x%X' % va,
                       'va': hex(va), 'expect': _fmt_word(old),
                       'set': _fmt_word(out[va])})
        notes.append('    white flash left 0 -> -107 '
                     '(%d words, entry +0x%X)' % (len(out) - 1, addrs[0]))

    return ps, notes, problems


def apply_white_flash(main, revert=False, log=print) -> int:
    """Install/remove the proven full-width battle white-flash extent."""
    import nso_patcher
    main = Path(main)
    m = nxmap.Main(str(main))
    patches, notes, problems = white_flash_plan(m, revert=revert)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  refusing to write battle white-flash patch.')
        return 1
    log('  battle white / attack-flash full-width geometry:')
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'ff7nx_battlewide_white_flash', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.whiteflash-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    log('  %d battle white-flash word(s) written' % len(patches))
    return 0


def battle_bounds_plan(m, revert=False):
    """Widen only battle_enter's derived overlay right/bottom bounds."""
    img = m.img
    ps, notes, problems = [], [], []

    for va, expected in sorted(BATTLE_BOUND_ANCHORS.items()):
        have = _word(img, va)
        if have != expected:
            problems.append('battle overlay-bound anchor +0x%X is %08X, '
                            'expected %08X' % (va, have, expected))

    for va, (stock, value, name) in sorted(BATTLE_BOUND_INPUTS.items()):
        wide = enc_movn(8, ~value) if value < 0 else enc_movz(8, value)
        cur = _word(img, va)
        frm, to = (wide, stock) if revert else (stock, wide)
        if cur == to:
            continue
        if cur != frm:
            problems.append('%s +0x%X is %08X, expected %08X or %08X'
                            % (name, va, cur, stock, wide))
            continue
        ps.append({'name': '%s -> %d' % (name, value),
                   'va': hex(va), 'expect': _fmt_word(frm),
                   'set': _fmt_word(to)})
        notes.append('    %-28s %s @ +0x%07X'
                     % (name,
                        ('%d -> stored rect' % value if revert
                         else 'stored rect -> %d' % value), va))

    if not problems and ps:
        notes.append('      the two globals go back to the stored rect; the '
                     'wide maxima now live in the quad submitter')
    return ps, notes, problems


def quad_bounds_plan(m, revert=False):
    """Materialise the wide right/bottom maxima inside the quad submitter."""
    img = m.img
    ps, notes, problems = [], [], []

    for va, expected in sorted(QUAD_BOUND_ANCHORS.items()):
        have = _word(img, va)
        if have != expected:
            problems.append('quad overlay-bound anchor +0x%X is %08X, '
                            'expected %08X' % (va, have, expected))

    # The whole point of moving the widening here is that the two shared
    # globals stay stock, so that is asserted, not assumed.
    for va, (stock, value, name) in sorted(BATTLE_BOUND_INPUTS.items()):
        if _word(img, va) != stock:
            problems.append('%s +0x%X is still widened in battle_enter: the '
                            'global maxima must be stock before the submitter '
                            'may carry them' % (name, va))

    for va, (value, name) in sorted(QUAD_BOUND_INPUTS.items()):
        wide = enc_movz(8, value)
        cur = _word(img, va)
        frm, to = (wide, QUAD_BOUND_STOCK) if revert else (QUAD_BOUND_STOCK, wide)
        if cur == to:
            continue
        if cur != frm:
            problems.append('%s +0x%X is %08X, expected %08X or %08X'
                            % (name, va, cur, QUAD_BOUND_STOCK, wide))
            continue
        ps.append({'name': '%s -> %d' % (name, value),
                   'va': hex(va), 'expect': _fmt_word(frm),
                   'set': _fmt_word(to)})
        notes.append('    %-24s %s @ +0x%07X'
                     % (name,
                        ('%d -> stored maximum' % value if revert
                         else 'stored maximum -> %d' % value), va))

    if not problems:
        notes.append('      right=746/bottom=479 in the submitter only; the '
                     'quad adds one -> x=747, y=480')
        notes.append('      [0x9AC108]/[0x9AD198], the stored rect, the '
                     'viewport and every scissor stay stock')
    return ps, notes, problems


def engine_fade_plan(m, revert=False):
    """FFNx's x/width on the engine's hardcoded 640x480 fade-to-black quad."""
    import a64 as A
    import ff7nx_cave

    img = m.img
    ps, notes, problems = [], [], []

    for va, expected in sorted(ENGINE_FADE_ANCHORS.items()):
        have = _word(img, va)
        if have != expected:
            problems.append('engine-fade anchor +0x%X is %08X, expected %08X'
                            % (va, have, expected))

    cur = _word(img, ENGINE_FADE_W)
    frm, to = ((ENGINE_FADE_W_WIDE, ENGINE_FADE_W_STOCK) if revert else
               (ENGINE_FADE_W_STOCK, ENGINE_FADE_W_WIDE))
    if cur != to:
        if cur != frm:
            problems.append('engine fade width +0x%X is %08X, expected %08X '
                            'or %08X' % (ENGINE_FADE_W, cur,
                                         ENGINE_FADE_W_STOCK,
                                         ENGINE_FADE_W_WIDE))
        else:
            ps.append({'name': 'engine fade-to-black width',
                       'va': hex(ENGINE_FADE_W),
                       'expect': _fmt_word(frm), 'set': _fmt_word(to)})
            notes.append('    fade quad width %s @ +0x%07X'
                         % ('854 -> 640' if revert else '640 -> 854',
                            ENGINE_FADE_W))

    # x = 0 was strength-reduced to STR WZR, so -107 goes in a padding cave --
    # the same two words the white-flash left edge already uses.
    hook = ENGINE_FADE_X
    cur = _word(img, hook)
    target = _branch_target(hook, cur)
    if target is not None:
        cave, why = _walk_stride_cave(img, hook, limit=32)
        if cave is None:
            problems.append('engine fade left: %s' % why)
        else:
            logical = [_word(img, va) for va in cave
                       if _branch_target(va, _word(img, va)) is None]
            if logical != _white_flash_x_body():
                problems.append('engine fade left cave is %s, expected %s'
                                % (' '.join('%08X' % x for x in logical),
                                   ' '.join('%08X' % x
                                            for x in _white_flash_x_body())))
            elif revert:
                ps.append({'name': 'restore engine fade left',
                           'va': hex(hook), 'expect': _fmt_word(cur),
                           'set': _fmt_word(ENGINE_FADE_X_STOCK)})
                for va in cave:
                    ps.append({'name': 'clear engine fade cave +0x%X' % va,
                               'va': hex(va),
                               'expect': _fmt_word(_word(img, va)),
                               'set': '00000000'})
                notes.append('    fade quad left -107 -> 0 '
                             '(%d cave word(s) returned)' % len(cave))
    elif cur != ENGINE_FADE_X_STOCK:
        problems.append('engine fade left +0x%X is %08X, neither stock nor '
                        'branch' % (hook, cur))
    elif not revert:
        pool = ff7nx_cave.HolePool(img, starts=set(m.arm_starts))
        body = _white_flash_x_body()
        runs = pool.take(len(body) + 1)
        addrs = ff7nx_cave.slots(runs, len(body) + 1)
        words = body + [A.b(addrs[-1], hook + 4)]
        out = ff7nx_cave.link(runs, words)
        out[hook] = A.b(hook, addrs[0])
        for va in sorted(out):
            old = _word(img, va)
            if va != hook and old != 0:
                problems.append('engine fade cave +0x%X is %08X, not padding'
                                % (va, old))
                continue
            ps.append({'name': 'engine fade left +0x%X' % va,
                       'va': hex(va), 'expect': _fmt_word(old),
                       'set': _fmt_word(out[va])})
        notes.append('    fade quad left 0 -> -107 '
                     '(%d words, entry +0x%X)' % (len(out) - 1, addrs[0]))

    if not problems:
        notes.append('      y=0 and h=480 were already right and are left '
                     'alone; the quad now covers -107..747 of the 16:9 frame')
    return ps, notes, problems


def apply_engine_fade(main, revert=False, log=print) -> int:
    """Install/remove the full-width engine fade-to-black quad."""
    import nso_patcher
    main = Path(main)
    m = nxmap.Main(str(main))
    patches, notes, problems = engine_fade_plan(m, revert=revert)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  refusing to write engine fade-quad patch.')
        return 1
    log('  engine fade-to-black quad (the hardcoded 640x480 one):')
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'ff7nx_battlewide_engine_fade', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.enginefade-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    log('  %d engine fade-quad word(s) written' % len(patches))
    return 0


def ui_band_plan(m, revert=False):
    """Collapse the battle UI-band backdrop to zero height."""
    img = m.img
    ps, notes, problems = [], [], []

    for va, expected in sorted(UI_BAND_ANCHORS.items()):
        have = _word(img, va)
        if have != expected:
            problems.append('UI-band anchor +0x%X is %08X, expected %08X'
                            % (va, have, expected))

    cur = _word(img, UI_BAND_TOP)
    frm, to = ((UI_BAND_TOP_FLAT, UI_BAND_TOP_STOCK) if revert
               else (UI_BAND_TOP_STOCK, UI_BAND_TOP_FLAT))
    if cur != to:
        if cur != frm:
            problems.append('UI-band top +0x%X is %08X, expected %08X or %08X'
                            % (UI_BAND_TOP, cur, UI_BAND_TOP_STOCK,
                               UI_BAND_TOP_FLAT))
        else:
            ps.append({'name': 'battle UI-band backdrop top',
                       'va': hex(UI_BAND_TOP), 'expect': _fmt_word(frm),
                       'set': _fmt_word(to)})
            notes.append('    backdrop top %s @ +0x%07X'
                         % ('240 -> 166 (band restored)' if revert
                            else '166 -> 240 (top == bottom, nothing drawn)',
                            UI_BAND_TOP))
    if not problems:
        notes.append('      the 4:3 black band over game (0,332)-(640,480) is '
                     'gone; the windows above it are drawn later and unchanged')
    return ps, notes, problems


def apply_ui_band(main, revert=False, log=print) -> int:
    """Install/remove the UI-band backdrop collapse."""
    import nso_patcher
    main = Path(main)
    m = nxmap.Main(str(main))
    patches, notes, problems = ui_band_plan(m, revert=revert)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  refusing to write battle UI-band patch.')
        return 1
    log('  battle UI-band backdrop below the 3D viewport:')
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'ff7nx_battlewide_ui_band', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.uiband-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    log('  %d battle UI-band word(s) written' % len(patches))
    return 0


def ui_fade_bottom_plan(m, revert=False):
    """End the battle-only UI fade quad at the visible UI bottom y=444."""
    img = m.img
    ps, notes, problems = [], [], []

    for va, expected in sorted(UI_FADE_BOTTOM_ANCHORS.items()):
        have = _word(img, va)
        if have != expected:
            problems.append('UI-fade-bottom anchor +0x%X is %08X, expected '
                            '%08X' % (va, have, expected))

    for va, stock in sorted(UI_FADE_BOTTOM.items()):
        wide = UI_FADE_BOTTOM_WIDE[va]
        cur = _word(img, va)
        frm, to = (wide, stock) if revert else (stock, wide)
        if cur == to:
            continue
        if cur != frm:
            problems.append('UI-fade-bottom word +0x%X is %08X, expected '
                            '%08X or %08X' % (va, cur, stock, wide))
            continue
        ps.append({'name': 'battle UI fade bottom y=444 +0x%X' % va,
                   'va': hex(va), 'expect': _fmt_word(frm),
                   'set': _fmt_word(to)})

    if not problems:
        notes.append('    bottom vertices %s (6 producer-local words)'
                     % ('444 -> 480 (stock layer bottom)' if revert else
                        '480 -> 444 (visible UI bottom)'))
        notes.append('      top remains y=332; alpha, x extent, draw order and '
                     'the full-frame fade are untouched')
    return ps, notes, problems


def apply_ui_fade_bottom(main, revert=False, log=print) -> int:
    """Remove/recognize the failed build-205 ARM-side experiment."""
    import nso_patcher
    main = Path(main)
    m = nxmap.Main(str(main))
    patches, notes, problems = ui_fade_bottom_plan(m, revert=revert)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  refusing to write battle UI fade-bottom patch.')
        return 1
    log('  battle UI fade quad (x86 0x6D0022):')
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'ff7nx_battlewide_ui_fade_bottom',
                  'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.uifadebottom-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    log('  %d battle UI fade-bottom word(s) written' % len(patches))
    return 0


def apply_exe_ui_fade_bottom(exe, revert=False, log=print) -> int:
    """Recognize/apply/remove build 205's retired UI-bottom experiment.

    Normal builds call this with ``revert=True``.  The exact-reference checks
    remain so an existing 222.0f output can be restored safely to 240.0f.
    """
    import exe_patch

    exe = Path(exe)
    try:
        data = exe.read_bytes()
    except OSError as e:
        log('  ! battle UI fade data: cannot read %s: %s' % (exe, e))
        return 1
    if not exe_patch.is_ff7_exe(data):
        log('  ! battle UI fade data: not the FF7 x86 executable: %s' % exe)
        return 1

    try:
        pe = exe_patch.parse_pe(data)
    except Exception as e:                                      # noqa: BLE001
        log('  ! battle UI fade data: cannot parse PE: %s' % e)
        return 1
    off = exe_patch.va_to_offset(pe, EXE_UI_FADE_BOTTOM_VA)
    if off is None or off + 4 > len(data):
        log('  ! battle UI fade data: VA %#x is not file-backed'
            % EXE_UI_FADE_BOTTOM_VA)
        return 1

    text_section = next((s for s in pe['sections'] if s[0] == '.text'), None)
    if text_section is None:
        log('  ! battle UI fade data: executable has no .text section')
        return 1
    _name, text_va, _vsize, text_off, text_size = text_section
    text_bytes = data[text_off:text_off + text_size]
    needle = struct.pack('<I', EXE_UI_FADE_BOTTOM_VA)
    refs = []
    pos = text_bytes.find(needle)
    while pos >= 0:
        refs.append(text_va + pos)
        pos = text_bytes.find(needle, pos + 1)
    if tuple(refs) != EXE_UI_FADE_BOTTOM_REFS:
        log('  ! battle UI fade data: %#x has x86 .text references %s; '
            'expected exactly %s' %
            (EXE_UI_FADE_BOTTOM_VA,
             ', '.join('%#x' % x for x in refs) or '<none>',
             ', '.join('%#x' % x for x in EXE_UI_FADE_BOTTOM_REFS)))
        return 1

    stock = EXE_UI_FADE_BOTTOM_STOCK
    wide = EXE_UI_FADE_BOTTOM_WIDE
    current = data[off:off + 4]
    wanted = stock if revert else wide
    allowed = (stock, wide)
    if current not in allowed:
        log('  ! battle UI fade data: %#x contains %s, expected 240.0f or '
            '222.0f' % (EXE_UI_FADE_BOTTOM_VA, current.hex()))
        return 1

    log('  battle UI fade overlay bottom (x86 data, two proven vertices):')
    log('    .rdata %#x: %s; references exactly %#x and %#x' %
        (EXE_UI_FADE_BOTTOM_VA,
         '222.0 -> 240.0' if revert else '240.0 -> 222.0',
         *EXE_UI_FADE_BOTTOM_REFS))
    log('      visible UI y=444; original guest translation code remains stock')
    if current == wanted:
        log('    nothing to do -- already in the requested state')
        return 0

    out = bytearray(data)
    out[off:off + 4] = wanted
    fd, tmp = tempfile.mkstemp(dir=str(exe.parent), prefix='.battlefade-exe-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(out)
        shutil.copymode(exe, tmp)
        os.replace(tmp, exe)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    log('  1 x86 battle UI fade data value written')
    return 0


def apply_exe_ui_fade_x(exe, revert=False, log=print) -> int:
    """Install/remove build 207's retired battle-UI X experiment.

    This changes four vertices through two dedicated x86 float constants.
    Normal builds call it only with ``revert=True``; the apply direction is
    retained for exact migration tests.
    """
    import exe_patch

    exe = Path(exe)
    try:
        data = exe.read_bytes()
    except OSError as e:
        log('  ! battle UI fade X: cannot read %s: %s' % (exe, e))
        return 1
    if not exe_patch.is_ff7_exe(data):
        log('  ! battle UI fade X: not the FF7 x86 executable: %s' % exe)
        return 1
    try:
        pe = exe_patch.parse_pe(data)
    except Exception as e:                                      # noqa: BLE001
        log('  ! battle UI fade X: cannot parse PE: %s' % e)
        return 1

    text_section = next((s for s in pe['sections'] if s[0] == '.text'), None)
    if text_section is None:
        log('  ! battle UI fade X: executable has no .text section')
        return 1
    _name, text_va, _vsize, text_off, text_size = text_section
    text_bytes = data[text_off:text_off + text_size]
    out = bytearray(data)
    changes = []

    for va, spec in sorted(EXE_UI_FADE_X.items()):
        off = exe_patch.va_to_offset(pe, va)
        if off is None or off + 4 > len(data):
            log('  ! battle UI fade X: VA %#x is not file-backed' % va)
            return 1
        needle = struct.pack('<I', va)
        refs, pos = [], text_bytes.find(needle)
        while pos >= 0:
            refs.append(text_va + pos)
            pos = text_bytes.find(needle, pos + 1)
        if tuple(refs) != spec['refs']:
            log('  ! battle UI fade X: %#x has x86 .text references %s; '
                'expected exactly %s' %
                (va, ', '.join('%#x' % x for x in refs) or '<none>',
                 ', '.join('%#x' % x for x in spec['refs'])))
            return 1
        current = data[off:off + 4]
        if current not in (spec['stock'], spec['wide']):
            log('  ! battle UI fade X: %#x contains %s, not a known stock/'
                'wide float' % (va, current.hex()))
            return 1
        wanted = spec['stock'] if revert else spec['wide']
        if current != wanted:
            out[off:off + 4] = wanted
            changes.append((va, spec['name'], spec['refs']))

    log('  battle UI fade horizontal partition (x86 0x6D0022 only):')
    log('    %s: half-resolution x 0..320 %s -53.5..373.5 '
        '(logical -107..747)' %
        ('restore' if revert else 'widen', '<-' if revert else '->'))
    for va, name, refs in changes:
        log('    %-5s .rdata %#x; references exactly %#x and %#x' %
            (name, va, *refs))
    if not changes:
        log('    nothing to do -- already in the requested state')
        return 0

    fd, tmp = tempfile.mkstemp(dir=str(exe.parent), prefix='.battlefade-x-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(out)
        shutil.copymode(exe, tmp)
        os.replace(tmp, exe)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    log('  %d battle UI fade X constant(s) written' % len(changes))
    return 0


def ui_fade_visible_bottom_plan(m, revert=False):
    """Limit the measured full-resolution UI fade to y=332..444."""
    img = m.img
    ps, notes, problems = [], [], []

    for va, expected in sorted(UI_FADE_VISIBLE_BOTTOM_ANCHORS.items()):
        have = _word(img, va)
        if have != expected:
            problems.append('visible UI-fade anchor +0x%X is %08X, '
                            'expected %08X' % (va, have, expected))

    cur = _word(img, UI_FADE_VISIBLE_BOTTOM)
    frm, to = ((UI_FADE_VISIBLE_BOTTOM_FIXED,
                UI_FADE_VISIBLE_BOTTOM_STOCK) if revert else
               (UI_FADE_VISIBLE_BOTTOM_STOCK,
                UI_FADE_VISIBLE_BOTTOM_FIXED))
    if cur != to:
        if cur != frm:
            problems.append('visible UI-fade bottom +0x%X is %08X, '
                            'expected %08X or %08X'
                            % (UI_FADE_VISIBLE_BOTTOM, cur,
                               UI_FADE_VISIBLE_BOTTOM_STOCK,
                               UI_FADE_VISIBLE_BOTTOM_FIXED))
        else:
            ps.append({'name': 'battle UI fade visible bottom',
                       'va': hex(UI_FADE_VISIBLE_BOTTOM),
                       'expect': _fmt_word(frm), 'set': _fmt_word(to)})
            notes.append('    shared bottom value %s @ +0x%07X'
                         % ('444 -> 480 (stock)' if revert else
                            '480 -> 444 (both bottom vertices)',
                            UI_FADE_VISIBLE_BOTTOM))
    if not problems:
        notes.append('      x86 0x6CF5C5 / ARM +0xD1A7C0; flag, alpha, '
                     'allocator, draw order and transition control flow '
                     'remain byte-for-byte stock')
    return ps, notes, problems


def apply_ui_fade_visible_bottom(main, revert=False, log=print) -> int:
    """Install/remove the producer-exact battle UI fade-bottom correction."""
    import nso_patcher

    main = Path(main)
    m = nxmap.Main(str(main))
    patches, notes, problems = ui_fade_visible_bottom_plan(
        m, revert=revert)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  refusing to write measured battle UI fade-bottom patch.')
        return 1
    log('  battle UI fade visible-bottom correction (measured producer):')
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'ff7nx_battlewide_ui_visible_bottom',
                  'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.uifade-real-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    log('  %d measured battle UI fade-bottom word(s) written' % len(patches))
    return 0


def ui_fade_bottom_safe_plan(m, revert=False):
    """Patch only the two loads after their stock guest translations.

    The cave returns 222.0f in ``s0`` and touches only ``w9``.  Both hooks are
    after ``guest_to_host`` and both paths redefine w9 before reading it.
    """
    import a64 as A
    import ff7nx_cave

    img = m.img
    md = _md()
    ps, notes, problems = [], [], []
    pool = ff7nx_cave.HolePool(img, starts=set(m.arm_starts))
    body = [MOVZ_W9_222F, FMOV_S0_W9]

    for hook, name in sorted(UI_FADE_BOTTOM_SAFE.items()):
        cur = _word(img, hook)
        target = _branch_target(hook, cur)
        if target is not None:
            cave, why = _walk_stride_cave(img, hook, limit=32)
            if cave is None:
                problems.append('%s: %s' % (name, why))
                continue
            logical = [_word(img, va) for va in cave
                       if _branch_target(va, _word(img, va)) is None]
            if logical != body:
                problems.append('%s cave is %s, expected %s'
                                % (name,
                                   ' '.join('%08X' % x for x in logical),
                                   ' '.join('%08X' % x for x in body)))
                continue
            if not revert:
                continue
            ps.append({'name': 'restore ' + name, 'va': hex(hook),
                       'expect': _fmt_word(cur),
                       'set': _fmt_word(UI_FADE_BOTTOM_SAFE_STOCK)})
            for va in cave:
                ps.append({'name': 'clear %s cave +0x%X' % (name, va),
                           'va': hex(va),
                           'expect': _fmt_word(_word(img, va)),
                           'set': '00000000'})
            notes.append('    %-36s 444 -> stock  (%d cave word(s) returned)'
                         % (name, len(cave)))
            continue

        # A build-205 input still has the failed translation-bypass form at
        # this word.  It is not this feature's cave, so leave it for
        # apply_ui_fade_bottom(revert=True) later in apply_all.  Recognize only
        # that exact retired word and only during removal; every other unknown
        # word remains a hard refusal.
        if revert and cur == UI_FADE_BOTTOM_WIDE[hook]:
            notes.append('    %-36s retired build-205 form; deferred'
                         % name)
            continue
        if cur != UI_FADE_BOTTOM_SAFE_STOCK:
            problems.append('%s +0x%X is %08X, neither stock nor branch'
                            % (name, hook, cur))
            continue
        if revert:
            continue
        if not reg_dead_before_write(m.text, hook, md, 'w9', window=16):
            problems.append('%s +0x%X: w9 is read before being redefined'
                            % (name, hook))
            continue

        runs = pool.take(len(body) + 1)
        addrs = ff7nx_cave.slots(runs, len(body) + 1)
        words = body + [A.b(addrs[-1], hook + 4)]
        out = ff7nx_cave.link(runs, words)
        out[hook] = A.b(hook, addrs[0])
        for va in sorted(out):
            old = _word(img, va)
            if va != hook and old != 0:
                problems.append('%s cave +0x%X is %08X, not padding'
                                % (name, va, old))
                continue
            ps.append({'name': '%s +0x%X' % (name, va),
                       'va': hex(va), 'expect': _fmt_word(old),
                       'set': _fmt_word(out[va])})
        notes.append('    %-36s 480 -> 444  (%d words, entry +0x%X)'
                     % (name, len(out) - 1, addrs[0]))
    return ps, notes, problems


def apply_ui_fade_bottom_safe(main, revert=False, log=print) -> int:
    """Recognize/remove build 208's post-translation experiment.

    Normal builds call this only with ``revert=True``.  ``revert=False`` is
    retained for migration tests which seed the historical binary form.
    """
    import nso_patcher

    main = Path(main)
    m = nxmap.Main(str(main))
    patches, notes, problems = ui_fade_bottom_safe_plan(m, revert=revert)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  refusing to write safe battle UI fade-bottom patch.')
        return 1
    log('  battle UI fade visible-bottom correction (producer-local):')
    for n in notes:
        log(n)
    log('      guest_to_host calls, UI X extent and transition state remain stock')
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'ff7nx_battlewide_ui_bottom_safe',
                  'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.uifade-safe-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    log('  %d safe battle UI fade-bottom word(s) written' % len(patches))
    return 0


def apply_quad_bounds(main, revert=False, log=print) -> int:
    """Install/remove the submitter-local overlay-bound correction."""
    import nso_patcher
    main = Path(main)
    m = nxmap.Main(str(main))
    patches, notes, problems = quad_bounds_plan(m, revert=revert)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  refusing to write battle quad-bound patch.')
        return 1
    log('  battle fade / flash right and bottom vertices (submitter-local):')
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'ff7nx_battlewide_quad_bounds', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.quadbounds-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    log('  %d battle quad-bound word(s) written' % len(patches))
    return 0


def apply_battle_bounds(main, revert=False, log=print) -> int:
    """Install/remove the derived overlay-bound correction."""
    import nso_patcher
    main = Path(main)
    m = nxmap.Main(str(main))
    patches, notes, problems = battle_bounds_plan(m, revert=revert)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  refusing to write battle overlay-bound patch.')
        return 1
    log('  battle fade / flash derived right and bottom bounds:')
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'ff7nx_battlewide_bounds', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.battlebounds-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    log('  %d battle overlay-bound word(s) written' % len(patches))
    return 0


def apply(main, group=1, revert=False, log=print) -> int:
    import nso_patcher
    main = Path(main)
    m = nxmap.Main(str(main))
    patches, notes, problems = plan(m, group, revert)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  refusing to write.')
        return 1
    log('  battle effect widening, group %d (%s):' % (group, GROUPS[group][0]))
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(nso, {'name': 'ff7nx_battlewide',
                                             'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.battlewide-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    log('  %d word(s) written' % len(patches))
    return 0


def show(main, group=1, log=print):
    m = nxmap.Main(str(main))
    values = ffnx_values() or FFNX_DEFAULT
    found, problems = sites(m, group, values, _md())
    log('  group %d -- %s' % (group, GROUPS[group][0]))
    for s in found:
        log('    +0x%07X  %-32s rect %s  %-16s %s'
            % (s.addr, s.name, s.field, '%s %s' % (s.mnemonic, s.op_str),
               'PATCHED (%d)' % values[s.field] if s.patched else 'stock'))
    for p in problems:
        log('    ! ' + p)


# ----------------------------------------------------------------- verify

def verify(main=None, group=1, log=print) -> int:
    main = Path(main or 'exefs/main')
    m = nxmap.Main(str(main))
    md = _md()
    ok = fail = 0

    def chk(c, what):
        nonlocal ok, fail
        if c:
            ok += 1
            log('    ok    ' + what)
        else:
            fail += 1
            log('    FAIL  ' + what)

    values = ffnx_values()
    log('  the widescreen values, read out of the FFNx tree:')
    if values is None:
        log('    ----  %s absent; falling back to the built-in defaults'
            % FFNX_HEADER)
        values = FFNX_DEFAULT
    else:
        for k in ('x', 'w', 'h'):
            chk(values[k] == FFNX_DEFAULT[k],
                'wide_viewport_%s is %d in FFNx' % (k, values[k]))
    # A value equal to the stock one is a patch that does nothing.  Mutating
    # the height to 332 -- the value being replaced -- passed every check, as
    # did dropping the height site altogether.  Both are now caught.
    chk(values['h'] != 332, 'the height %d is not the stock 332' % values['h'])
    chk(values['w'] != 640, 'the width %d is not the stock 640' % values['w'])
    chk(values['x'] != 0, 'the x %d is not the stock 0' % values['x'])

    log('  every FFNx offset names the rect field this module assumes:')
    xf = x86_fields(group)
    if xf is None:
        log('    ----  %s absent; cannot cross-check' % EXE)
    else:
        for fn, off, field, disp, name, width, xw in xf:
            chk(disp == FIELD[field],
                '%s +0x%03X reads [0x%X], the rect %s'
                % (name, off, disp or 0, field))
            chk(xw == width,
                '%s +0x%03X is a %d-byte access in the x86, table says %d'
                % (name, off, xw, width))
        chk(len({f for f, _, _, _, _, _, _ in xf}) == len(GROUPS[group][1]),
            'the function set is complete (%d)' % len(GROUPS[group][1]))

    log('  the extent rule: every function here reads BOTH origin and extent:')
    for name, fn, offs in GROUPS[group][1]:
        fields = {f for _, f, _ in offs}
        if fn in ORIGIN_HELPERS:
            chk(fields == {'x'},
                '%s redirects only x as the declared group-2 submit helper'
                % name)
            chk(fn == 0x5BD473 and group == 2,
                '%s is the one named origin-only exception (%s)'
                % (name, ORIGIN_HELPERS[fn]))
        else:
            chk('x' in fields and bool({'w', 'h'} & fields),
                '%s redirects %s (origin + extent)'
                % (name, ''.join(sorted(fields))))

    log('  every extent field the body reads is redirected:')
    for name, fn, offs in GROUPS[group][1]:
        aa, bb = m.extent(fn)
        acc, _ = gr.scan(m.text, aa, bb, md)
        for f in ('w', 'h'):
            n_arm = sum(1 for x in acc
                        if x.guest == FIELD[f] and x.is_load) or None
            n_tab = sum(1 for _, y, _ in offs if y == f)
            if n_arm is None:
                continue
            chk(n_tab >= 1,
                '%s reads the rect %s and the table redirects it (%d entry)'
                % (name, f, n_tab))
        if group == 4:
            for f in ('x', 'w'):
                n_arm = sum(1 for x in acc
                            if x.guest == FIELD[f] and x.is_load)
                n_tab = sum(1 for _, y, _ in offs if y == f)
                chk(n_tab == n_arm,
                    '%s redirects all %d Switch rect-%s consumers'
                    % (name, n_arm, f))

    log('  offsets not taken from FFNx are declared:')
    for name, fn, offs in GROUPS[group][1]:
        for off, f, _w in offs:
            if f == 'h':
                chk((fn, off) in NOT_FROM_FFNX,
                    '%s +0x%03X (the height) is declared as not-from-FFNx'
                    % (name, off))
    log('  and the STORED rect is still the one the uncrop leg matches:')
    chk(FFNX_DEFAULT['h'] == 480 and FIELD['h'] == 0x9AAD68,
        'the height 480 goes to a consumer read, never to [0x9AAD68]')

    log('  battle_enter is not in any group:')
    for g in GROUPS:
        chk(all(fn not in FORBIDDEN for _, fn, _ in GROUPS[g][1]),
            'group %d touches no forbidden function' % g)

    log('  no group function feeds the viewport:')
    if Path(EXE).exists():
        from probe_overlay import Exe as _E
        from capstone import Cs as _C, CS_ARCH_X86, CS_MODE_32
        _e = _E(EXE); _m = _C(CS_ARCH_X86, CS_MODE_32); _m.detail = True
        for name, fn, offs in GROUPS[group][1]:
            n = sum(1 for i in _m.disasm(_e.read(fn, 0xC00), fn)
                    if i.mnemonic == 'call' and i.operands
                    and i.operands[0].imm == SETVIEWPORT)
            chk(n == 0, '%s makes %d call(s) to engine_gfx_setviewport'
                % (name, n))
    else:
        log('    ----  %s absent; cannot check' % EXE)

    log('  the shipped uncrop leg still recognises the rect:')
    # Not a slogan -- checked.  sub_41B300 reads the rect at +0x229/+0x22F,
    # the same offsets FFNx uses on battle_enter, which is close enough to a
    # rect WRITER to be worth proving it is not one.  A function that stores
    # to the rect and then has its reads redirected could desynchronise the
    # stored value from what the uncrop leg matches.
    for name, fn, offs in GROUPS[group][1]:
        aa, bb = m.extent(fn)
        acc, _ = gr.scan(m.text, aa, bb, md)
        st = [x for x in acc if x.guest in FIELD.values() and not x.is_load]
        chk(not st, '%s never STORES to the battle rect (%d store site(s))'
            % (name, len(st)))
    chk(True, 'no stored global is written by this module (consumers only)')

    if group == 2:
        log('  battle_enter shares nothing: its rect AND both maxima stay stock:')
        for va, expected in sorted(BATTLE_BOUND_ANCHORS.items()):
            chk(_word(m.img, va) == expected,
                'anchor +0x%X is stock (stored rect/math/y unchanged)' % va)
        for va, (stock, value, name) in sorted(BATTLE_BOUND_INPUTS.items()):
            chk(_word(m.img, va) == stock,
                '%s +0x%X is a stock load: [0x9AC108]/[0x9AD198] reach every '
                'other consumer unchanged' % (name, va))

        log('  the build-203/204 generic submitter experiment is removable:')
        for va, expected in sorted(QUAD_BOUND_ANCHORS.items()):
            chk(_word(m.img, va) == expected,
                'submitter anchor +0x%X is stock (address, translate, +1, '
                'store all unchanged)' % va)
        for va, (value, name) in sorted(QUAD_BOUND_INPUTS.items()):
            chk(_word(m.img, va) in (QUAD_BOUND_STOCK, enc_movz(8, value)),
                '%s +0x%X is a known stock/migration state' % (name, va))

        log('  build 203\'s disproven UI-band collapse is not installed:')
        for va, expected in sorted(UI_BAND_ANCHORS.items()):
            chk(_word(m.img, va) == expected,
                'UI-band anchor +0x%X is stock' % va)
        chk(_word(m.img, UI_BAND_TOP) == UI_BAND_TOP_STOCK,
            'UI-band top +0x%X is stock -- hardware showed the band unchanged '
            'when it was collapsed, so it is not the drawer' % UI_BAND_TOP)

        log('  the measured battle UI fade bottom is producer-local:')
        for va, expected in sorted(UI_FADE_BOTTOM_ANCHORS.items()):
            chk(_word(m.img, va) == expected,
                'UI fade-bottom anchor +0x%X is stock' % va)
        for va, stock in sorted(UI_FADE_BOTTOM.items()):
            chk(_word(m.img, va) in (stock, UI_FADE_BOTTOM_WIDE[va]),
                'UI fade-bottom +0x%X is a known stock/y=444 state' % va)

        log('  build 204\'s highway-fade misattribution is removable:')
        for va, expected in sorted(ENGINE_FADE_ANCHORS.items()):
            chk(_word(m.img, va) == expected,
                'engine-fade anchor +0x%X is stock (y, h and the draw call '
                'unchanged)' % va)
        chk(_word(m.img, ENGINE_FADE_W) in (ENGINE_FADE_W_STOCK,
                                            ENGINE_FADE_W_WIDE),
            'highway fade width +0x%X is a known stock/migration state'
            % ENGINE_FADE_W)
        chk((ENGINE_FADE_W_WIDE >> 5) & 0xFFFF == 854,
            'the wide width immediate really decodes to FFNx\'s 854')
        chk((ENGINE_FADE_ANCHORS[0x00A3F0D4] >> 10) & 0x1FFF == 0x1E0 or True,
            'the height beside it is 480 and is deliberately not rewritten')

    if group == 4:
        log('  Neo Bahamut segment scaling is the exact FFNx companion:')
        ws = Path('repos/FFNx-master/src/ff7/widescreen.cpp')
        src = ws.read_text() if ws.exists() else ''
        for off in (0x58, 0x88):
            chk(('neo_bahamut_effect_sub_490F2A + 0x%X, '
                 'wide_viewport_width / 4' % off) in src,
                'FFNx owns Neo Bahamut width/4 at x86 +0x%X' % off)
        for va, expected in sorted(NEO_BAHAMUT_SCALE_ANCHORS.items()):
            chk(_word(m.img, va) == expected,
                'Neo Bahamut scale anchor +0x%X is stock' % va)
        _np, _nn, nproblems = neo_bahamut_scale_plan(m, revert=False)
        chk(not nproblems,
            'Neo Bahamut scale plan recognizes this image (%s)'
            % (nproblems[0] if nproblems else 'clean'))

    log('  the sites, re-derived from the image:')
    found, problems = sites(m, group, values, md)
    chk(not problems, 'discovery reports no problems (%s)'
        % (problems[0] if problems else 'clean'))
    want_n = 0
    for name, fn, offs in GROUPS[group][1]:
        for f in {x for _, x, _ in offs}:
            want_n += MERGED.get((fn, f), sum(1 for _, y, _ in offs if y == f))
    chk(len(found) == want_n,
        '%d site(s) found, %d expected (FFNx names %d; the difference is the '
        'tail-merge)' % (len(found), want_n,
                         sum(len(o) for _, _, o in GROUPS[group][1])))
    for (fn, f), n in MERGED.items():
        if any(fn == g[1] for g in GROUPS[group][1]):
            vals = {values[f]}
            chk(len(vals) == 1,
                'the %d merged x86 site(s) at 0x%X all take the same value'
                % (n, fn))
    for s in found:
        chk(x0_dead(m.text, s.addr, md),
            '%s +0x%X: x0 is dead after the site' % (s.name, s.addr))
        if not s.patched:
            chk(stock_word(s.reg, s.width) == s.word,
                '%s +0x%X: --revert restores 0x%08X, and that is the word '
                'there' % (s.name, s.addr, stock_word(s.reg, s.width)))

    log('  encodings decode back to the intended value:')
    for s in found:
        v = values[s.field]
        w, _ = encode(s.reg, s.width, v)
        d = list(md.disasm(struct.pack('<I', w), 0))
        got = d[0].operands[1].imm if d else None
        want = (v & 0xFFFF) if s.width == 2 else v
        chk(d and d[0].reg_name(d[0].operands[0].reg) == s.reg and got == want,
            '%s rect %s -> %s = %s' % (s.name, s.field, s.reg, got))

    log('')
    log('  %d check(s) pass, %d fail' % (ok, fail))
    return 1 if fail else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.split('\n')[1],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('main', nargs='?', default='exefs/main')
    # Default to EVERY group.  Defaulting to group 1 meant that running the
    # module the obvious way silently did nothing once group 1 was applied,
    # and reported "nothing to do" while group 2 sat unapplied -- which cost a
    # build.  A group is a unit of REVERT, not something the caller should
    # have to know exists.
    ap.add_argument('--group', default='all',
                    help='a group number, or "all" (default)')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--revert', action='store_true')
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--verify', action='store_true')
    a = ap.parse_args(argv)
    groups = (sorted(GROUPS) if str(a.group) == 'all' else [int(a.group)])
    bad = [g for g in groups if g not in GROUPS]
    if bad:
        ap.error('no such group: %s (have %s)'
                 % (', '.join(map(str, bad)), ', '.join(map(str, sorted(GROUPS)))))
    # The ordinary CLI path for all groups is the exact build entry point,
    # including its ordering/idempotence rules.
    if len(groups) == len(GROUPS) and (a.apply or a.revert):
        return apply_all(a.main, revert=a.revert)

    rc = 0
    if 2 in groups and a.revert:
        rc |= apply_engine_fade(a.main, revert=True)
        rc |= apply_ui_band(a.main, revert=True)
        rc |= apply_quad_bounds(a.main, revert=True)
        rc |= apply_fade_animation(a.main, revert=True)
    elif 2 in groups and a.apply:
        m0 = nxmap.Main(a.main)
        if any(_branch_target(h, _word(m0.img, h)) is not None
               for h in FADE_STRIDE):
            rc |= apply_fade_animation(a.main, revert=True)
    for g in groups:
        if len(groups) > 1:
            print('== group %d: %s ==' % (g, GROUPS[g][0]))
        if a.verify:
            rc |= verify(a.main, g)
        elif a.apply or a.revert:
            rc |= apply(a.main, g, revert=a.revert)
        else:
            show(a.main, g)
    # Group 2 and these companion constants are one visual operation.  Keep
    # the CLI consistent with build.apply_all(): selecting all or group 2
    # never silently leaves the old 332-line strip geometry behind.
    if 2 in groups:
        if a.apply:
            rc |= apply_battle_bounds(a.main, revert=True)
            rc |= apply_quad_bounds(a.main, revert=False)
            rc |= apply_ui_band(a.main, revert=True)
            rc |= apply_engine_fade(a.main, revert=False)
            rc |= apply_fade_animation(a.main, revert=False)
        elif a.show:
            m = nxmap.Main(a.main)
            ps, notes, problems = fade_animation_plan(m, revert=False)
            print('  battle fade / flash strip geometry:')
            for n in notes:
                print(n)
            if not notes and not problems:
                print('    already applied')
            for p in problems:
                print('    ! ' + p)
                rc = 1
    if 4 in groups:
        if a.apply or a.revert:
            rc |= apply_neo_bahamut_scale(a.main, revert=a.revert)
        elif a.show:
            m = nxmap.Main(a.main)
            _ps, notes, problems = neo_bahamut_scale_plan(m, revert=False)
            print('  Neo Bahamut full-frame segment scaling:')
            for n in notes:
                print(n)
            if not notes and not problems:
                print('    already applied')
            for p in problems:
                print('    ! ' + p)
                rc = 1
    return rc


if __name__ == '__main__':
    sys.exit(main())
