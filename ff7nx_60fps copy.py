#!/usr/bin/env python3
"""
ff7nx_60fps.py -- generate the FF7 Switch 60 FPS patch set from stock files.

Two outputs, both LayeredFS, base title 0100A5B00BDC6000:

  romfs/ff7/resources/ff7_1.02/ff7_en   <- data patches (framerate limiters)
  exefs/main                            <- code patches (ARM64 recompilation)

Usage
-----
    python3 ff7nx_60fps.py --exe /path/to/stock/ff7_en \
                           --nso /path/to/stock/exefs/main \
                           --out ./sdout

    python3 ff7nx_60fps.py --exe ... --nso ... --verify     # check only

Both inputs must be STOCK. Every patch verifies the original bytes first
and aborts on mismatch, so a wrong or already-patched input fails loudly
instead of producing a corrupt file.

Requires: pip install lz4
"""
import argparse, hashlib, os, re, struct, sys

try:
    import lz4.block
except ImportError:
    sys.exit('need lz4:  pip install lz4  (or --break-system-packages)')

_BSS_GROW = 0
TITLE_ID = '0100A5B00BDC6000'
BUILD_ID = '8CAAD5A4E142D2B8EBC1811B5AF05125'


def d(x):
    return struct.pack('<d', x)


# --------------------------------------------------------------------------
# ff7_en -- data patches. Confirmed working on hardware.
#
# Each limiter computes  frame_time = countspersecond / divisor  once at mode
# init, then busy-waits against it. These divisors are dedicated constants:
# 0x7B7840 has exactly one reference in .text; 0x7C0B00 has two and both are
# inside the battle limiter's own setup.
# --------------------------------------------------------------------------
# Confirmed on hardware: the user observed 60 FPS in both field and battle
# with exactly these three bytes changed.
EXE_CONFIRMED = [
    ('field limiter divisor 30.0 -> 60.0',  0x7B7840, d(30.0), d(60.0)),
    ('battle limiter divisor 15.0 -> 60.0', 0x7C0B00, d(15.0), d(60.0)),
    ('60fps mod compatibility flag',        0x914B21, b'\x00', b'\x01'),
]

def f(x):
    return struct.pack('<f', x)


# Everything below is DERIVED BUT UNPROVEN. Off unless explicitly enabled.
EXE_GATED = {
    # animations.cpp, "Enemy death - disintegrate 2":
    #   patch_divide_code<float>((uint32_t)ff7_externals.field_float_battle_7B7680,
    #                            battle_frame_multiplier);
    # A .rdata float, so it belongs to the exe rather than the NSO -- which is
    # why it was resolved but never wired into a group. Without it the
    # disintegrate-2 death had its integer constants scaled and its rate float
    # left alone, i.e. exactly the half-applied state that looks wrong.
    #
    # FFNx's own comment warns "float value used also elsewhere", so this is a
    # shared constant by FFNx's own admission. It ships with r-enemy_death
    # because that subsystem is incomplete without it, but if enemy deaths look
    # right and something unrelated looks wrong, this is the first suspect.
    'r-enemy_death': [
        ('disintegrate-2 death rate 22.5 -> 5.625', 0x7B7680, f(22.5), f(5.625)),
    ],
    # FFNx patch_divide_code<double>(swirl_loop_sub_4026D4, ...) targets.
    # These are .rdata doubles rather than code, so they live in the exe.
    'swirl': [
        ('swirl timing 0.75 -> 0.375',      0x7B63E8, d(0.75), d(0.375)),
        ('swirl timing 5.0 -> 2.5',         0x7B63F0, d(5.0),  d(2.5)),
        ('swirl timing 0.1 -> 0.05',        0x7B63F8, d(0.1),  d(0.05)),
        ('swirl timing 8.0 -> 4.0',         0x7B6400, d(8.0),  d(4.0)),
    ],
}

# --------------------------------------------------------------------------
# main NSO -- code patches against the ARM64 recompilation of ff7_en.
#
# The Switch build never executes the x86 .text. `main` holds an ahead-of-time
# ARM64 translation of all 10,952 functions, dispatched through a map at module
# offset 0x126D3A8 (16-byte records: u32 x86_va, u32 pad, u64 arm64_ptr).
# Each offset below came from resolving FFNx's own address derivation against
# the exe, then locating the matching immediate in the translated body.
#
# Offsets are module-relative (0 = start of .text).
# --------------------------------------------------------------------------
#
# THE ONE RULE THIS FILE NOW ENFORCES
# ----------------------------------
# Only patches confirmed on real hardware are on by default. Everything else
# is in a named group and off until asked for. The previous session lost the
# ability to interpret any test result because unproven patches accumulated in
# the unconditional list, so a null result could not be distinguished from a
# patch whose effect an earlier unproven patch had already consumed.
#
# HANDOFF 5g, restated: if you cannot say what a build changes relative to the
# last one you tested, the test tells you nothing.
NSO_CONFIRMED = [
    # ---- field walk/run speed -------------------------------------------
    # REMOVED 2026-07-30. In field_update_single_model_position (x86
    # 0x636C41) the per-frame step is
    #     delta = (direction_delta * movement_speed) >> 8
    # computed twice (X at x86 0x636FE5, Y at 0x63700D). The claim that
    # changing the final shift from >>8 to >>9 "halves the step exactly,
    # with no precision loss" is false: ASR is a truncating shift, and
    # stock's own >>8 already floors any product under 256 to zero. Moving
    # to >>9 doubles the size of that dead zone to <512. southmk2 (No.5
    # Reactor) scripts Cloud's backward walk with a product that lands
    # inside the new dead zone but outside the old one -- delta becomes
    # exactly 0 every tick, forever, instead of the intended 1, and the
    # scene can never reach the batle opcode that follows it. Isolated by
    # bisection: the freeze survives in a build with every other patch
    # group stripped out and only this shift plus the two limiter divisors
    # applied, and disappears when this shift alone is dropped.
    #
    # This is the exact failure the FIRST walk-speed patch (WALKFIX.md) was
    # built to prevent -- "a plain shift would turn movement_speed = 1 into
    # 0 ... so add +1 first." That cave rounded the wrong field
    # (offset_position_y at guest +0x42, not movement_speed at +0x76 -- see
    # PRODUCTION_READINESS.md), so the rounding protection never actually
    # reached movement. When the fix was redone as a plain shift-immediate
    # edit on the real site, the rounding step wasn't carried over.
    #
    # Replaced below (search "round-to-nearest") with a 3-word cave per axis
    # that biases the product by half the new divisor before shifting, at
    # the same two sites. That keeps the "no precision loss" property stock
    # itself already relied on (nothing under 256 ever moved, at any frame
    # rate) instead of silently doubling the threshold.
]

# --------------------------------------------------------------------------
# Hand-derived groups that were never isolated on hardware.
# --------------------------------------------------------------------------
NSO_GATED = {
    # x86 sets  byte [0xBFD0F0] = 14  at four hardcoded sites (0x5C0EBA,
    # 0x5C199A, 0x5C1CE6, 0x5D42A8). That global is the shared battle timing
    # source: opcode 0xC5 copies it into a model's waitFrames, and the camera
    # setup at 0x42C31C uses it BOTH as the move's frame counter and as the
    # divisor for the per-frame step (step = delta / wait), so scaling it moves
    # both sides together and preserves step * counter = delta.
    #
    # That reasoning is sound and the patch still did nothing observable. It is
    # the single most likely candidate for actively causing harm, because it
    # stretches EVERY battle script wait, not just camera moves. Test it alone,
    # and test the camera groups with it OFF.
    #
    # 14 -> 56. Stock encodings are ORR bitmask forms; MOVZ is equivalent for a
    # 32-bit register.
    'script-wait': [
        ('g_script_wait_frames 14 -> 56 (1 of 4)',   0x007DF1FC, 0x321F0BF3, 0x52800713),
        ('g_script_wait_frames 14 -> 56 (2 of 4)',   0x007E1054, 0x321F0BF3, 0x52800713),
        ('g_script_wait_frames 14 -> 56 (3 of 4)',   0x007E1D40, 0x321F0BF3, 0x52800713),
        ('g_script_wait_frames 14 -> 56 (4 of 4)',   0x00833800, 0x321F0BE8, 0x52800708),
    ],
    # FFNx: patch_divide_code<byte>(battle_fps_menu_multiplier, 4).
    # The resolver does NOT reproduce this one (FFNx points at a byte its
    # linear-disassembly scan does not see as an immediate operand), so it is
    # the least corroborated patch in the file.
    'fps-menu': [
        ('battle FPS menu multiplier 4 -> 1',        0x00090AE8, 0x321E03E8, 0x52800028),
    ],
    # Exactly the patch set the previous session shipped and the user ran for
    # weeks, minus the two confirmed words that are now always applied. Enabled
    # by --legacy. Reproducing this byte-for-byte matters: it is the only build
    # with real hardware history, so it is the baseline every comparison should
    # be made against.
    #
    # Verification target from the old handoff: with this group enabled and
    # nothing else, `main` must come out md5 9be265eaeb77cbed428dd8f88c50fd16
    # and `ff7_en` md5 6932643ecf84b1a1ddef48c71515e131.
    'legacy': [
        ('battle FPS menu multiplier 4 -> 1',        0x00090AE8, 0x321E03E8, 0x52800028),
        ('swirl fade count 46 -> 50',                0x000130B4, 0x7100B928, 0x7100C928),
        ('swirl clamp 78 -> 127',                    0x000133B8, 0x71013928, 0x7101FD28),
        ('field ladder/jump mult -16000 -> -4000',   0x009D8720, 0x1287CFFC, 0x1281F3FC),
        ('field ladder/jump step /4 -> /2 (1 of 2)', 0x009D9B30, 0x13027D08, 0x13017D08),
        ('field ladder/jump step /4 -> /2 (2 of 2)', 0x009DB0CC, 0x13027D08, 0x13017D08),
        ('g_script_wait_frames 14 -> 56 (1 of 4)',   0x007DF1FC, 0x321F0BF3, 0x52800713),
        ('g_script_wait_frames 14 -> 56 (2 of 4)',   0x007E1054, 0x321F0BF3, 0x52800713),
        ('g_script_wait_frames 14 -> 56 (3 of 4)',   0x007E1D40, 0x321F0BF3, 0x52800713),
        ('g_script_wait_frames 14 -> 56 (4 of 4)',   0x00833800, 0x321F0BE8, 0x52800708),
    ],

    # The two swirl .text words (fade count 46->50 at +0x130B4, clamp 78->127
    # at +0x133B8) and all three ladder/jump words (+0x9D8720, +0x9D9B30,
    # +0x9DB0CC) used to be listed here by hand. ff7nx_resolve.py now derives
    # every one of them independently and byte-identically -- see its
    # self-validation output -- so they live in `r-swirl` and `r-field_models`
    # instead. Keeping a second hand-typed copy would only create a way for the
    # two to drift apart. `r-swirl` additionally covers swirl_main_loop +0x79
    # and +0x184, which the hand pass missed.
}

# --------------------------------------------------------------------------
# Machine-resolved groups from ff7nx_resolve.py. Generated, not typed: each
# hook was found by mapping an FFNx patch spec through the x86->ARM64
# recompilation map, and the generator refuses ambiguous and shared-constant
# cases rather than picking one. Also off by default.
# --------------------------------------------------------------------------
try:
    from ff7nx_patchgroups import PATCH_GROUPS as RESOLVED_GROUPS
    from ff7nx_patchgroups import PARTIAL_GROUPS as RESOLVED_PARTIAL
    from ff7nx_patchgroups import CODE_PAIRED_GROUPS as RESOLVED_PAIRED
except ImportError:
    RESOLVED_GROUPS, RESOLVED_PARTIAL, RESOLVED_PAIRED = {}, {}, {}

for _g, _ps in RESOLVED_GROUPS.items():
    NSO_GATED.setdefault('r-' + _g, []).extend(_ps)

# `p-` groups touch a function where we can only scale SOME of the constants
# FFNx scales. That is not "a smaller benefit" -- it is desynchronisation.
# Three of the five battle_sub_5B9EC2 colour constants made enemies render
# white until the frame counter wrapped. Excluded from --enable-all.
PARTIAL = set()
for _g, _ps in RESOLVED_PARTIAL.items():
    NSO_GATED.setdefault('p-' + _g, []).extend(_ps)
    PARTIAL.add('p-' + _g)

# `c-` groups sit in a function whose BODY FFNx replaces, or whose internal data
# pointer FFNx repoints at a table only FFNx has. The constants are correct in
# isolation and still wrong in effect, because the logic they time is logic FFNx
# no longer runs. Boss death and disintegrate-1 death are here -- they are why
# "some of the enemy vanquish effects look screwed up". Excluded from
# --enable-all.
CODE_PAIRED = set()
for _g, _ps in RESOLVED_PAIRED.items():
    NSO_GATED.setdefault('c-' + _g, []).extend(_ps)
    CODE_PAIRED.add('c-' + _g)


# --------------------------------------------------------------------------
# Groups that are INCOMPLETE BY CONSTRUCTION.
#
# FFNx does not fix these subsystems with constants alone -- it also swaps a
# data-table pointer or replaces a whole function. We can do the constants and
# not the rest, and a half-applied subsystem is worse than an unpatched one.
# Excluded from --enable-all; still selectable by name for experiments.
#
# Reported symptoms that traced back to exactly this:
#   r-battle_damage  -> damage numbers drawn in several places at once
#   r-field_fade     -> the outgoing scene flashes again before going dark
# --------------------------------------------------------------------------
INCOMPLETE = {
    # animations.cpp 1248-1257: +0x54 scales how long a damage number lives,
    # but FFNx ALSO repoints +0x1E2 and +0x2D7 at a different y-offset table
    # (y_pos_offset_display_damage_60) so the numbers stack correctly. That
    # table is FFNx-allocated memory; we have nothing to point at. Scaling the
    # lifetime without it leaves several numbers on screen simultaneously --
    # reported as "damage in multiple locations on the screen".
    'p-battle_damage':
        'needs the y_pos_offset_display_damage_60 table pointer, which only '
        'exists inside FFNx',
    # field.cpp 316-318 scale the fade frame counts. In the same block FFNx
    # also does patch_code_dword(execute_opcode_table[FADE], opcode_script_FADE)
    # and replace_call_function(execute_opcode_table[NFADE] + 0x89, ...).
    # Scaling the counts without replacing the opcode handlers desynchronises
    # the fade from the screen copy -- reported as the outgoing scene flashing
    # again before it goes dark.
    'r-field_fade':
        'FFNx pairs these with a replaced FADE opcode handler and an NFADE '
        'bank divide',
}

# Things constants alone cannot fix on this platform, recorded so nobody spends
# another session on them. Each needs a function replacement, which we cannot
# do: a cave that skips a translated function corrupts the guest stack.
CANNOT_FIX_WITH_CONSTANTS = {
    'field jump speed':
        'replace_call_function(execute_opcode_table[JUMP] + 0x1F1, ...)',
    'elevator / scripted scroll speed':
        'replace_call_function on SCRLA/SCR2DC/SCR2DL/SCRLP bank values',
    'scripted battle camera flow':
        'execute_camera_functions is replaced wholesale and is stateful',
    'boss death, disintegrate-1 death':
        'replace_function(battle_boss_death_sub_5BC5EC / _5BC04D)',
}

# --------------------------------------------------------------------------
# Battle camera / scripted-effect pacing.
#
# x86 0x42C31C sets up interpolation as  step = delta / g_script_wait_frames
# (guest 0xBFD0F0), four times per axis plus a 0x1000 fraction.  The move then
# runs for that many frames -- so at 4x the tick rate it arrives 4x early.
# FFNx solves this by multiplying g_script_wait_frames by battle_frame_
# multiplier (4 at 60 FPS) in the 0xC6 handler.
#
# We do the same at the point of use.  Each sdiv is preceded by
#     sxtw x9, w9          ; sign-extend the divisor
# and SBFIZ sign-extends *and* shifts in one instruction, so
#     sbfiz x9, w9, #2, #32
# multiplies the divisor by 4 with no cave and no spare register.
#
# Only affects the eight divides inside this one function; enemy attacks and
# limit breaks that move the camera are exactly what it governs.
# --------------------------------------------------------------------------
# Battle camera MOVEMENT stepping.
#
# x86 0x42C31C computes  step = delta / frames  for each axis. At 4x the tick
# rate the camera reaches its destination 4x early. Each sdiv is preceded by
#     sxtw x9, w9            (93407D29)
# and SBFIZ sign-extends *and* shifts in one instruction, so
#     sbfiz x9, w9, #2, #32  (937E7D29)
# multiplies the divisor by 4 -- the move now spans 4x as many frames.
#
# MUST be paired with the camdat*.bin patch (camera script 0xF5 waits x4).
# Alone, this makes the move smooth but the script cuts it short. The waits
# alone make the camera dash then idle -- the "move, stop, move, stop"
# stutter. Both together keep movement and script timing in step.
#
# Known side effect: 0x42C31C is shared with the in-battle stats menu
# slide-in, which is driven by an unscaled frame count, so that menu opens
# ~4x slower. Cosmetic; revisit by scaling its own wait source instead.
# IMPORTANT: this and the camdat 0xF5 scaling act on the SAME quantity.
# 0x42C31C computes  step = delta / g_script_wait_frames , and the camdat
# patch already multiplies that wait. Enabling both multiplies twice --
# x4 and x4 is x16, which reads as a sluggish, out-of-sync camera.
#
# Off by default. Use --cam-step to enable, and only with --cam-mult 1.
BATTLE_CAM_SITES = [0x000DB378, 0x000DB414, 0x000DB4B0, 0x000DB514,
                    0x000DB7C8, 0x000DB864, 0x000DB900, 0x000DB964]


def battle_cam_patches(mult):
    """sxtw x9,w9 -> sbfiz x9,w9,#shift,#32, multiplying the divisor."""
    shift = {2: 1, 4: 2, 8: 3}.get(mult)
    if shift is None:
        raise SystemExit('ABORT  --cam-step multiplier must be 2, 4 or 8')
    new = 0x93400000 | (((-shift) % 64) << 16) | (31 << 10) | (9 << 5) | 9
    return [('battle camera step /%d (%d of %d)' % (mult, i, len(BATTLE_CAM_SITES)),
             off, 0x93407D29, new)
            for i, off in enumerate(BATTLE_CAM_SITES, 1)]

# --------------------------------------------------------------------------
# Code caves.  .text has no internal padding, but the segment ends at
# 0x1152660 while .rodata starts at 0x1153000 -- the rest of that page is
# already mapped executable.  We extend the segment's declared size and put
# stubs there, then branch to them from the hook site.
#
# ORIG is replaced with the instruction we displaced, BACK with a branch to
# hook+4.  Everything else is a literal ARM64 word.
# --------------------------------------------------------------------------
ORIG, BACK = 'ORIG', 'BACK'

# Nothing needs a cave yet -- the walk-speed fix turned out to be a shift
# constant (see NSO_PATCHES).  An earlier build hooked guest 0xCC16E8 here
# believing it was movement_speed; it is the adjacent walkmesh triangle
# index, fed to the table at [0xCFF744] with stride 0x18.  That cave was
# wrong and has been removed.
NSO_CAVES = []


# --------------------------------------------------------------------------
# camdat0/1/2.bin -- battle camera scripts.
#
# Loaded straight from romfs, not from an LGP: ff7_en 0x42A111 pushes
# 0x9A13BC as the destination and picks the filename from the table at
# 0x7C2528 (camdat0, camdat0, camdat1, camdat2, camdat2, ...).
#
# Opcode 0xF5 sets "frames to wait"; FFNx's simulateCameraScript scales that
# operand by battle_frame_multiplier. Battle is natively 15 FPS, so running
# it at 60 means x4.
#
# We match the three-byte sequence `F5 <n> F4` -- set-wait immediately
# followed by the wait opcode -- rather than walking the index tables, which
# do not sit where the game's pointer arithmetic implies (file 0x10 is script
# data, not pointers). 0xFF is a wait-forever sentinel and 0x00 a no-op, so
# both are skipped; results clamp at 255 so nothing wraps.
#
# Pair with the battle camera step patches in NSO_PATCHES. Waits alone make
# the camera dash then idle; steps alone make it glide but get cut short.
# --------------------------------------------------------------------------
CAMDAT_FILES = ('camdat0.bin', 'camdat1.bin', 'camdat2.bin')


def patch_camdat(data, mult):
    """Scale every `F5 <n> F4` wait operand. Returns (bytes, found, changed)."""
    data = bytearray(data)
    found = changed = 0
    i = 0
    while i < len(data) - 2:
        if (data[i] == 0xF5 and data[i + 2] == 0xF4
                and data[i + 1] not in (0x00, 0xFF)):
            found += 1
            new = min(data[i + 1] * mult, 255)
            if new != data[i + 1]:
                data[i + 1] = new
                changed += 1
            i += 3
        else:
            i += 1
    return bytes(data), found, changed


# --------------------------------------------------------------------------
# battle.lgp `?ab` -- battle animation scripts.
#
# Traced from the game: run_animation_script(actorID, ptrToScriptTable) is
# called at 0x42A6A3 with ptrToScriptTable = *(u32*)modelPtr + 0x68, where
# modelPtr = [actorIdx*4 + 0xBFB2B8]. That lands on the `?ab` entry, whose
# script pointer table sits at file offset 0x68. Entry count is
# (first_entry - 0x68) / 4; unused slots point at zero padding.
#
# The 60 FPS mod ships 391 `?da` files at 4x frames but NO `?ab` files, so
# the scripts still wait the vanilla number of frames while the animations
# they wait on became four times longer. That is the limit-break desync:
# the script reaches the damage opcode while the swing is still playing.
#
# FFNx fixes it at runtime -- 0xC6 sets the global wait, 0xF4 the per-actor
# wait, both scaled by battle_frame_multiplier. We bake the same scaling in.
#
# Only scripts that parse cleanly end to end are modified. A script we cannot
# fully walk is left exactly as-is, so a parser gap costs a fast enemy, never
# a corrupt archive. Operands are byte-sized and clamp at 255, and nothing
# changes length -- so the LGP is patched in place, no repacking.
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# The opcode table, transcribed one-for-one from FFNx
# src/ff7/battle/animations.h `numArgsOpCode`, with the switch-handled cases in
# `run_animation_script` folded in.
#
# THE BUG THIS REPLACES, AND WHY IT WAS INVISIBLE
# -----------------------------------------------
# The previous table had 36 entries covering 0x8E..0xB5. The real one has 110
# and runs to 0xFF. Everything above 0xB5 -- 0xC6 and 0xF4 aside -- was unknown,
# and the walker treated an unknown opcode as
#
#     a = ANIM_ARGS.get(op)
#     if a is None:
#         return waits, True        # <-- "clean"
#
# So a script died at its first 0xC7 or 0xE8 and reported SUCCESS. Measured on a
# stock battle.lgp: 3,576 of 4,013 scripts stopped at an unknown opcode after a
# MEDIAN OF ONE BYTE, and every one of them was counted as cleanly parsed. The
# guard in patch_battle_lgp -- "only scripts that parse cleanly end to end are
# modified" -- was doing nothing, because nothing ever failed to parse.
#
# Result: 76 wait operands scaled out of 725. The `?ab` scripts were, in
# practice, unscaled. The animations they wait on ARE four times longer (the
# 60 FPS mod's ?da files), so the scripts ran through their waits four times too
# early -- the character starts the next beat while the previous one is still
# playing. That is the limit-break aura still going while the character charges.
#
# With the real table: 4,002 of 4,013 scripts parse end to end (99.7%) and 725
# waits are found. The 11 that refuse are left untouched.
#
# VALUES
#   0..0x8D   play-animation index. FFNx's switch falls through to `default`,
#             does not find it in the table, and ends the FRAME -- the script
#             resumes at the next byte next frame. So: zero operands, keep going.
#   int       that many operand bytes
#   'W'       one operand byte, and it is a WAIT: this is what gets scaled.
#             0xC6 sets the global wait (later copied by 0xC5), 0xF4 sets the
#             actor's wait directly. FFNx scales both, and clamps at 255 --
#             which is why a static byte patch is exactly equivalent here, clamp
#             and all, unlike the camdat camera waits where the field is wider
#             than the operand.
#   'S'       handled by the switch, consumes no operand bytes.
#   'P'       0xFE: PEEKS the next byte without consuming it. If it is 0xC0 the
#             script restarts into the idle animation, so that is a terminator.
#   'T'       0xEE / 0xFF: the script ends.
#   0xF1      terminal. It is in FFNx's endingOpCode AND has -1 args, so the
#             position moves BACK one and it re-reads itself forever. Treating
#             it as terminal is both the faithful reading and strictly safer --
#             it raises clean parses from 99.4% to 99.7%, because scripts that
#             would otherwise run off the end of an entry now stop correctly.
#             The other endingOpCode members (0xA2 0xA7 0xA9 0xB6) end the frame
#             but do advance, so walking continues through them.
ANIM_OPS = {
    0x8E: 0, 0x8F: 0, 0x90: 3, 0x91: 1, 0x92: 0, 0x93: 0, 0x94: 5, 0x95: 0,
    0x96: 2, 0x97: 2, 0x98: 1, 0x99: 6, 0x9A: 4, 0x9B: 0, 0x9C: 0, 0x9D: 1,
    0x9E: 'S', 0x9F: 0, 0xA0: 1, 0xA1: 2, 0xA2: 1, 0xA3: 1, 0xA4: 0, 0xA5: 0,
    0xA6: 0, 0xA7: 1, 0xA8: 2, 0xA9: 2, 0xAA: 0, 0xAB: 4, 0xAC: 1, 0xAD: 5,
    0xAE: 0, 0xAF: 1, 0xB0: 0, 0xB1: 0, 0xB2: 0, 0xB3: 'S', 0xB4: 0, 0xB5: 11,
    0xB6: 1, 0xB7: 0, 0xB8: 0, 0xB9: 1, 0xBA: 2, 0xBC: 1, 0xBD: 4, 0xBE: 1,
    0xBF: 2, 0xC1: 'S', 0xC2: 1, 0xC3: 0, 0xC4: 3, 0xC5: 0, 0xC6: 'W',
    0xC7: 3, 0xC8: 5, 0xC9: 0, 0xCA: 'S', 0xCB: 8, 0xCC: 1, 0xCD: 0,
    0xCE: 'S', 0xCF: 8, 0xD0: 3, 0xD1: 5, 0xD2: 0, 0xD3: 0, 0xD4: 3, 0xD5: 8,
    0xD6: 1, 0xD7: 2, 0xD8: 3, 0xDA: 1, 0xDB: 4, 0xDC: 3, 0xDD: 2, 0xDE: 2,
    0xDF: 0, 0xE0: 0, 0xE1: 0, 0xE2: 0, 0xE3: 0, 0xE4: 0, 0xE5: 0, 0xE6: 0,
    0xE7: 1, 0xE8: 0, 0xE9: 3, 0xEA: 0, 0xEB: 'S', 0xEC: 'S', 0xED: 0,
    0xEE: 'T', 0xF0: 0, 0xF1: 'T', 0xF2: 0, 0xF3: 'S', 0xF4: 'W', 0xF5: 1,
    0xF6: 0, 0xF7: 1, 0xF8: 1, 0xF9: 0, 0xFA: 0, 0xFB: 4, 0xFC: 0, 0xFD: 6,
    0xFE: 'P', 0xFF: 'T',
}
# 0xCD is not in FFNx's table -- it is only ever the target 0xCE scans forward
# to. Reached directly it is a nop, and including it as one is what clears the
# last 175 unknown-opcode refusals.


# Opcodes that END THE PASS rather than the script. FFNx's interpreter sets
# `isScriptActive = false` on each of these and leaves the script position
# where it is, to be resumed next frame:
#
#   0x9E   wait until the acting actor's effects are finished
#   0xF3   the wait loop -- decrement waitFrames and come back
#   0xEB   \ hold while an effect is still loading
#   0xEC   /
#
# They matter here because a script whose last real opcode is one of them
# needs no terminator: the entry's trailing zero padding IS its end. Seven
# scripts in a stock battle.lgp are written that way -- and they are all seven
# of BARRET's limit breaks, slots 60..66 of `ruab`:
#
#   ... F0 9D 06 32 9E  00 00 00 ... 00  52 55 41 43
#                       ^ padding        ^ "RUAC", the next LGP entry
#
# Without this rule the walk runs through the padding, through the next
# entry's NAME, and off the end, and `walk_anim_script` refuses -- correctly,
# since it cannot account for those bytes. The consequence was that Barret's
# limit animations were the only ones in the game left at vanilla wait counts,
# four times too fast, silently, in every build.
#
# The rule is deliberately narrow. Of the eleven scripts a stock archive
# refuses, exactly these seven end on a pass-ender; the other four end on
# 0x0D, 0x0A and 0xC1 and stay refused, because running out of data after an
# opcode that does NOT end a pass means the walk lost sync, and guessing is
# what this function exists not to do.
PASS_ENDERS = (0x9E, 0xF3, 0xEB, 0xEC)


def walk_anim_script(b, base, lim):
    """
    Walk one `?ab` script. Returns (wait_operand_offsets, ok).

    `ok` is True only if the walk reached a real terminator inside the entry,
    or ran out of real data immediately after an opcode that ends the pass
    (see PASS_ENDERS). An unknown opcode is a REFUSAL, not a success -- that
    inversion is what hid the coverage problem, so it is worth being explicit:
    if this function returns True, every byte from `base` to the end was
    accounted for as an opcode or an operand, and the offsets in `waits` are
    operand bytes of 0xC6 or 0xF4 and nothing else.
    """
    # The physical end of the entry's real data. LGP pads each entry out to
    # the next one, so the padding is not script and must not be walked.
    end = lim
    while end > base and b[end - 1] == 0:
        end -= 1
    p = 0
    waits = []
    last = None
    for _ in range(8000):
        if base + p >= end:
            return waits, last in PASS_ENDERS
        if base + p >= lim:
            return waits, False
        op = b[base + p]
        last = op
        p += 1
        if op < 0x8E:                       # play-animation index
            continue
        a = ANIM_OPS.get(op)
        if a is None:
            return waits, False             # unknown -- refuse, do not guess
        if a == 'T':
            return waits, True
        if a == 'P':                        # 0xFE peeks, never consumes
            if base + p < lim and b[base + p] == 0xC0:
                return waits, True          # restart into idle = end of script
            continue
        if a == 'W':
            if base + p >= lim:
                return waits, False
            waits.append(base + p)
            p += 1
            continue
        if a == 'S':                        # switch-handled, no operands
            continue
        p += a
    return waits, False


def _legacy_walk_anim_script(b, base, lim):
    """
    The PREVIOUS, broken walker. Kept for exactly one purpose: detecting an
    archive that was scaled by it.

    It had 36 of the 110 opcodes and returned `ok=True` on an unknown one, so it
    reached only 76 of the 725 wait operands and scaled those. An archive in
    that state is genuinely dangerous: `battle_lgp_looks_patched` sees 17.7% of
    waits divisible by 4 against 9.9% for a stock archive and reports NOT
    patched, so the new walker would scale those 76 a second time -- x16 -- and
    the only symptom would be a handful of battle beats crawling.

    Do not reuse this for anything else.
    """
    ARGS = {0x8E: 0, 0x8F: 0, 0x90: 3, 0x91: 1, 0x92: 0, 0x93: 0, 0x94: 5,
            0x95: 0, 0x96: 2, 0x97: 2, 0x98: 1, 0x99: 6, 0x9A: 4, 0x9B: 0,
            0x9C: 0, 0x9D: 1, 0x9F: 0, 0xA0: 1, 0xA1: 2, 0xA2: 1, 0xA3: 1,
            0xA4: 0, 0xA5: 0, 0xA6: 0, 0xA7: 1, 0xA8: 2, 0xA9: 2, 0xAA: 0,
            0xAB: 4, 0xAC: 1, 0xAD: 5, 0xAE: 0, 0xAF: 1, 0xB0: 0, 0xB1: 0,
            0xB2: 0, 0xB4: 0, 0xB5: 11}
    END = {0xA2, 0xA7, 0xA9, 0xB6, 0xF1}
    NOARG = {0x9E, 0xEB, 0xEC, 0xF3, 0xFE, 0xC5}
    p, waits = 0, []
    for _ in range(4000):
        if base + p >= lim:
            return waits, False
        op = b[base + p]
        p += 1
        if op in (0xEE, 0xFF):
            return waits, True
        if op < 0x8E:
            continue
        if op in (0xC6, 0xF4):
            if base + p >= lim:
                return waits, False
            waits.append(base + p)
            p += 1
            continue
        if op in (0xC1, 0xCA):
            p = 0
            while base + p < lim and b[base + p] != 0xC9:
                p += 1
            if base + p >= lim:
                return waits, False
            p += 1
            continue
        if op in (0xB3, 0xCE):
            t = 0xB2 if op == 0xB3 else 0xCD
            while base + p < lim and b[base + p] != t:
                p += 1
            if base + p >= lim:
                return waits, False
            p += 1
            continue
        if op in NOARG:
            continue
        a = ARGS.get(op)
        if a is None:
            return waits, True                 # the bug: unknown == "clean"
        p += a
        if op in END:
            return waits, True
    return waits, False


def _wait_sets(data, walker):
    out = set()
    for name, off, size in lgp_entries(data):
        if len(name) != 4 or not name.lower().endswith('ab') or size < 0x6C:
            continue
        first = struct.unpack('<I', data[off + 0x68:off + 0x6C])[0]
        cnt = (first - 0x68) // 4
        if cnt <= 0 or cnt > 512 or 0x68 + 4 * cnt > size:
            continue
        seen = set()
        for k in range(cnt):
            o = struct.unpack('<I', data[off + 0x68 + 4 * k:
                                         off + 0x6C + 4 * k])[0]
            if not (0 < o < size) or o in seen:
                continue
            seen.add(o)
            if data[off + o] == 0:
                continue
            w, ok = walker(data, off + o, off + size)
            if ok:
                out |= set(w)
    return out


def battle_lgp_scaled_by_old_walker(data, mult):
    """
    True if this archive was scaled by the previous, broken walker.

    The signature is unambiguous and cannot fire on a stock archive: the waits
    the OLD walker could reach are almost all divisible by `mult`, while the
    ones only the CORRECT walker reaches are not. On a stock archive both
    populations sit near 10%.

    Returns (is_old_scaled, n_old, frac_old, n_new_only, frac_new_only).
    """
    old = _wait_sets(data, _legacy_walk_anim_script)
    new = _wait_sets(data, walk_anim_script)
    only_new = new - old
    both = new & old
    if len(both) < 20 or len(only_new) < 50:
        return False, len(both), 0.0, len(only_new), 0.0
    f_old = sum(1 for w in both if data[w] % mult == 0) / len(both)
    f_new = sum(1 for w in only_new if data[w] % mult == 0) / len(only_new)
    return (f_old > 0.90 and f_new < 0.50), len(both), f_old, len(only_new), f_new


def lgp_entries(data):
    """Yield (name, payload_offset, size) from an LGP, header parse only."""
    count = struct.unpack('<i', data[12:16])[0]
    for i in range(count):
        e = data[16 + 27 * i: 16 + 27 * (i + 1)]
        name = e[:20].split(b'\0')[0].decode('ascii', 'replace')
        off = struct.unpack('<I', e[20:24])[0]
        size = struct.unpack('<I', data[off + 20:off + 24])[0]
        yield name, off + 24, size


def patch_battle_lgp(data, mult, log=print):
    """Scale 0xC6/0xF4 wait operands in every cleanly-parsed `?ab` script."""
    data = bytearray(data)
    files = scripts = clean = skipped = scaled = found = 0
    for name, off, size in lgp_entries(bytes(data)):
        if len(name) != 4 or not name.lower().endswith('ab'):
            continue
        if size < 0x6C:
            continue
        files += 1
        first = struct.unpack('<I', data[off + 0x68:off + 0x6C])[0]
        cnt = (first - 0x68) // 4
        if cnt <= 0 or cnt > 512 or 0x68 + 4 * cnt > size:
            continue
        seen = set()
        for k in range(cnt):
            o = struct.unpack('<I', data[off + 0x68 + 4 * k:
                                         off + 0x6C + 4 * k])[0]
            if not (0 < o < size) or o in seen:
                continue
            seen.add(o)
            if data[off + o] == 0x00:        # unused slot
                continue
            scripts += 1
            waits, okp = walk_anim_script(data, off + o, off + size)
            if not okp:
                skipped += 1
                continue
            clean += 1
            found += len(waits)
            for w in waits:
                new = min(data[w] * mult, 255)
                if new != data[w]:
                    data[w] = new
                    scaled += 1
    pct = 100.0 * clean / scripts if scripts else 0.0
    log('  ok  battle.lgp  %d ?ab files, %d scripts (%d clean %.1f%%, %d '
        'refused), %d wait operand(s) found, %d scaled x%d'
        % (files, scripts, clean, pct, skipped, found, scaled, mult))
    # A parser regression here is silent and expensive: it does not crash, it
    # just quietly scales almost nothing and the battle scripts stay at 15 FPS
    # timing while the animations they wait on are four times longer. The
    # previous table parsed 1.5% of scripts to completion and reported all of
    # them as clean. Refuse to ship that.
    if scripts and pct < 95.0:
        raise SystemExit(
            'ABORT  only %.1f%% of ?ab scripts parsed end to end (%d of %d).\n'
            '       The opcode table in ANIM_OPS does not match this archive. '
            'Scaling a\n'
            '       fraction of the waits is worse than scaling none: the '
            'scripts desync\n'
            '       against animations that ARE four times longer.'
            % (pct, clean, scripts))
    return bytes(data)


def battle_lgp_looks_patched(data, mult):
    """
    Detect an ALREADY-SCALED battle.lgp, so it cannot be scaled a second time.

    This is a real footgun: the scaling is not idempotent, and pointing
    --battle-lgp at the output of a previous run multiplies the waits again
    (x4 becomes x16), which would look like "battle animations are far too
    slow" with no other symptom to trace it by.

    The test is decisive rather than clever. In a stock archive the wait
    operands are ordinary small numbers -- 15 dominates, and only about a
    quarter happen to be divisible by 4. After scaling by 4, essentially every
    wait is divisible by 4 by construction, and some have hit the 255 clamp. A
    threshold of 90% separates the two cases by a wide margin.

    Returns (looks_patched, n_waits, fraction_divisible, n_clamped).
    """
    n = div = clamped = 0
    for name, off, size in lgp_entries(data):
        if len(name) != 4 or not name.lower().endswith('ab') or size < 0x6C:
            continue
        first = struct.unpack('<I', data[off + 0x68:off + 0x6C])[0]
        cnt = (first - 0x68) // 4
        if cnt <= 0 or cnt > 512 or 0x68 + 4 * cnt > size:
            continue
        seen = set()
        for k in range(cnt):
            o = struct.unpack('<I', data[off + 0x68 + 4 * k:
                                         off + 0x6C + 4 * k])[0]
            if not (0 < o < size) or o in seen:
                continue
            seen.add(o)
            if data[off + o] == 0:
                continue
            waits, okp = walk_anim_script(data, off + o, off + size)
            if not okp:
                continue
            for w in waits:
                n += 1
                if data[w] % mult == 0:
                    div += 1
                if data[w] == 255:
                    clamped += 1
    frac = (div / n) if n else 0.0
    return (n >= 20 and frac > 0.90), n, frac, clamped


# --------------------------------------------------------------------------
# Battle camera throttle.
#
# execute_camera_functions (x86 0x5BF27D -> ARM64 0x7D36C0) is the per-frame
# dispatcher: it walks 16 slots and calls each registered camera function,
# which advances its move one step and decrements its counter. The whole
# system assumes it runs at 15 Hz; we run it at 60.
#
# FFNx rescales n_frames per function on first execution, keyed on which of
# five functions was registered, with a 16-slot "is new" flag array. That is
# stateful and needs real injected logic.
#
# Instead we throttle: run the dispatcher's body only 1 frame in N. Every
# camera move then advances at 1/N rate and takes N times the wall clock,
# and crucially  step * counter = delta  is untouched -- neither side is
# rescaled, so this cannot produce the dash-then-idle or crawl-and-cut-off
# failures that scaling one side did.
#
# The counter lives at the end of BSS (zero-initialised, writable). We grow
# bssSize by 8, so nothing existing moves. x16/x17 are IP0/IP1, free at a
# function entry point.
# --------------------------------------------------------------------------
# The five camera functions FFNx singles out for rescaling (verified: every
# name-encoded address resolves to itself on this build). These INTERPOLATE
# toward a target over n_frames. Every other camera function TRACKS -- it
# recomputes an absolute position each frame -- so throttling those freezes
# the camera at a stale position while the actor keeps moving. That was the
# "stuck, top-down, missing frames" failure of the whole-dispatcher throttle.
CAM_FNS = [
    ('position_5C3D0D', 0x007ED930, 0xA9BC5FF8),
    ('position_5C5B9C', 0x007F0990, 0xF81D0FF5),
    ('position_5C557D', 0x007EF100, 0xA9BA6FFC),
    ('focal_5C5F5E',    0x007F1D30, 0xF81D0FF5),
    ('focal_5C5714',    0x007F0050, 0xA9BB67FA),
]
CAM_THROTTLE_HOOK = 0x007D36C0
RET_INSN = 0xD65F03C0


def _cam_throttle_cave(cave, counter, orig, mask, hook):
    def adrp(rd, pc, tgt):
        imm = (tgt >> 12) - (pc >> 12)
        return 0x90000000 | ((imm & 3) << 29) | (((imm >> 2) & 0x7FFFF) << 5) | rd
    page, off = counter & ~0xFFF, counter & 0xFFF
    w = [adrp(16, cave, page),
         0xB9400000 | ((off >> 2) << 10) | (16 << 5) | 17,   # ldr  w17,[x16,#off]
         0x11000000 | (1 << 10) | (17 << 5) | 17,            # add  w17,w17,#1
         0x12000000 | (mask << 10) | (17 << 5) | 17,         # and  w17,w17,#mask
         0xB9000000 | ((off >> 2) << 10) | (16 << 5) | 17,   # str  w17,[x16,#off]
         0, orig, 0, RET_INSN]
    w[5] = 0x35000000 | ((((cave + 8 * 4) - (cave + 5 * 4)) >> 2 & 0x7FFFF) << 5) | 17
    w[7] = 0x14000000 | ((((hook + 4) - (cave + 7 * 4)) >> 2) & 0x3FFFFFF)
    return w



# --------------------------------------------------------------------------
# Battle camera n_frames scaling  (--cam-nframes N)
#
# camera_fn_data = guest 0xBFCE08, stride 0x28, n_frames at +4 (0xBFCE0C).
# FFNx multiplies n_frames by battle_frame_multiplier on a camera function's
# first execution. Four of its five functions scale n_frames and NOTHING
# else, meaning they recompute their step from remaining distance each
# frame -- so there is no step*counter=delta invariant to break here, unlike
# the 0x42C31C path that defeated every earlier attempt.
#
# n_frames has 10 writes. Three are decrements (a read ~11 bytes earlier);
# the rest are initialisations. We scale initialisations only.
#
# The recompiler builds the address as  w9 = idx*0x28 + base ; w0 = w9+0x2E4
# so there is no MOVZ/MOVK of 0xBFCE0C to search for -- these were found by
# tracking the hoisted base register symbolically. All four are the same
# shape: strh w19,[x0] (79000013).
#
# Cave is the SAFE pattern only: branch out, shift, replay the displaced
# store, branch back. No control flow change -- a cave that skips a
# translated function corrupts the guest stack (see HANDOFF 5b).
# --------------------------------------------------------------------------
CAM_NFRAMES_SITES = [0x007E8D40, 0x007E9D40, 0x007EAFCC, 0x007E5D2C]
CAM_NFRAMES_STRH = 0x79000013          # strh w19, [x0]


def _nframes_cave(cave, orig, hook, shift):
    lsl = 0x53000000 | (((32 - shift) % 32) << 16) | ((31 - shift) << 10) | (19 << 5) | 19
    b_back = 0x14000000 | ((((hook + 4) - (cave + 2 * 4)) >> 2) & 0x3FFFFFF)
    return [lsl, orig, b_back]


# --------------------------------------------------------------------------
# Post-call return-value scalers  (--enable opcode-scale / nfade)
#
# FFNx does not reimplement these field opcode handlers. It wraps the single
# call to get_bank_value inside each one and scales the returned value:
#
#   short ff7_opcode_multiply_get_bank_value(short bank, short address) {
#       int16_t ret = ff7_externals.get_bank_value(bank, address);
#       if (is_fps_running_more_than_original()) ret *= get_frame_multiplier();
#       return ret;
#   }
#
# get_frame_multiplier() returns common_frame_multiplier = 2 at 60 FPS. These
# are the values that drive scripted movement DURATION, which is why the
# animations look right while the movement they belong to finishes early:
# the jump lands before the arc ends, the elevator arrives twice as fast.
#
# Why this is safe where the camera throttle was not
# --------------------------------------------------
# The guest return value is not in a host register -- it is in the guest CPU
# context, at [ctx + 0] (guest EAX). So "wrap the callee" becomes "adjust a
# memory slot after the callee returns", which needs no function replacement
# and no change to control flow. Nothing is skipped, so no guest return
# address is left un-popped (HANDOFF 5b).
#
# The displaced instruction is always the guest-ESP reload that follows a
# translated call, `ldr w8, [ctx, #0x10]`. It is position-independent, and its
# base register tells us which register holds the context in that function
# (x20, x21 and x22 all occur). The resolver refuses any site whose displaced
# instruction is not exactly that shape.
#
# x16/x17 are IP0/IP1. Immediately after a `bl` returns they are dead by the
# AAPCS -- the callee is free to clobber x0-x18 -- so they are safe scratch
# here without saving. The translated bodies confirm it: their epilogues
# restore only x19 and up.
# --------------------------------------------------------------------------
try:
    from ff7nx_patchgroups import OPCODE_SITES
except ImportError:
    OPCODE_SITES = []

W8, TMP, TMP2 = 8, 16, 17


def _ldrsh(rt, rn, imm=0):
    # LDRSH (immediate), 32-bit destination: size=01, opc=11.
    # opc=10 is the 64-bit form (`ldrsh x16, ...`) -- benign here but not what
    # is meant, and test_bitmask.py rejects it.
    return 0x79C00000 | ((imm >> 1) << 10) | (rn << 5) | rt


def _strh(rt, rn, imm=0):
    return 0x79000000 | ((imm >> 1) << 10) | (rn << 5) | rt


def _lsl(rd, rn, sh):
    return 0x53000000 | (((32 - sh) % 32) << 16) | ((31 - sh) << 10) | (rn << 5) | rd


def _asr(rd, rn, sh):
    return 0x13000000 | (sh << 16) | (31 << 10) | (rn << 5) | rd


def _add_imm(rd, rn, imm):
    return 0x11000000 | (imm << 10) | (rn << 5) | rd


def _subs_imm(rd, rn, imm):
    return 0x71000000 | (imm << 10) | (rn << 5) | rd


def _adds_imm(rd, rn, imm):
    return 0x31000000 | (imm << 10) | (rn << 5) | rd


def _csel(rd, rn, rm, cond):
    return 0x1A800000 | (rm << 16) | (cond << 12) | (rn << 5) | rd


def _b(frm, to):
    return 0x14000000 | (((to - frm) >> 2) & 0x3FFFFFF)


def _bcond(frm, to, cond):
    return 0x54000000 | ((((to - frm) >> 2) & 0x7FFFF) << 5) | cond


def _adrp(rd, pc, tgt):
    imm = (tgt >> 12) - (pc >> 12)
    return 0x90000000 | ((imm & 3) << 29) | (((imm >> 2) & 0x7FFFF) << 5) | rd


def _ldr_w(rt, rn, imm):
    return 0xB9400000 | ((imm >> 2) << 10) | (rn << 5) | rt


def _str_w(rt, rn, imm):
    return 0xB9000000 | ((imm >> 2) << 10) | (rn << 5) | rt


def _tbz(rt, bit, frm, to):
    b5, b40 = (bit >> 5) & 1, bit & 0x1F
    imm14 = ((to - frm) >> 2) & 0x3FFF
    return (b5 << 31) | (0b011011 << 25) | (0 << 24) | (b40 << 19) | (imm14 << 5) | rt


def _add_reg(rd, rn, rm):
    # ADD Wd, Wn, Wm (shifted-register form, shift #0)
    return 0x0B000000 | (rm << 16) | (rn << 5) | rd


def _sub_reg(rd, rn, rm):
    # SUB Wd, Wn, Wm (shifted-register form, shift #0)
    return 0x4B000000 | (rm << 16) | (rn << 5) | rd


def _and_imm(rd, rn, mask):
    # AND Wd, Wn, #mask -- LSB-aligned contiguous one-bits only (0xff,
    # 0x1ff: exactly the shapes this file needs). N=0, immr=0,
    # imms = popcount-1. Verified against capstone: and_imm(9,9,0xff) ==
    # the real stock word 0x12001D29 at 0x9dd590.
    ones = bin(mask).count('1')
    if mask != (1 << ones) - 1:
        raise ValueError('_and_imm only supports contiguous LSB masks')
    imms = ones - 1
    return 0x12000000 | (imms << 10) | (rn << 5) | rd


def _movz(rd, imm, shift=0):
    hw = (shift // 16) & 0x3
    return 0x52800000 | (hw << 21) | ((imm & 0xFFFF) << 5) | rd


COND_GE, COND_LE, COND_LT = 0xA, 0xD, 0xB

# --------------------------------------------------------------------------
# field walk/run step -- tick-gate replacement for both the deleted
# >>8 -> >>9 immediate patch and the sign-aware-round cave that replaced it.
#
# Both prior attempts changed the SIZE of each per-tick step. FF7's own
# field_check_collision_with_target decides "arrived" with a per-axis
# sign-of(position - target) test, not a distance check -- that only
# converges cleanly for the exact step size stock's 30Hz loop was tuned
# for. Changing step size can make it oscillate across the target forever
# (walking in place, animation flipping) instead of landing on it, which is
# what happened even after the dead-zone bug itself was fixed.
#
# This version changes CADENCE instead of SIZE: every tick it runs the
# bit-exact stock >>8 computation, untouched, then throws the result away
# on every *other* tick (a real once-per-tick counter, not a per-hook
# counter, so two models moving in the same tick can't desync it -- see
# WALK_TICK_HOOK below). The model takes the exact same sequence of steps,
# of the exact same sizes, as stock always did, just spread across twice as
# many real ticks -- so collision/arrival logic sees the identical delta
# sequence it always has, and total real-world move time matches stock.
#
# This is the same principle FFNx itself uses for this exact function, but
# FFNx implements it by skipping the real call outright and caching the
# return value -- not safe on this recompiler (HANDOFF 5b: a translated
# call site pushes a guest return address that only the callee's own
# translated body pops; skipping the call leaks it and crashed on entering
# battle previously). This cave never skips a call and never changes
# control flow outside the hooked function -- always replay, always branch
# back, only the VALUE differs by tick parity -- the same safe shape as
# every other cave in this file.
#
# All three hook words independently re-confirmed against the real stock
# NSO this session (not just carried over from earlier analysis):
#     0x009DD598  asr w28, w8, #8   (X)      word 0x13087D1C
#     0x009DD60C  asr w27, w8, #8   (Y)      word 0x13087D1B
#     0x009D6900  sub sp, sp, #0x70          word 0xD101C3FF
#         (field_update_models_positions entry -- confirmed exactly one
#         caller, 0x00948E48, i.e. it runs once per real field tick)
#
# NOT YET CONFIRMED ON HARDWARE. Test southmk2's backward-walk scene first
# (should arrive cleanly, no jitter), then a normal walk/run elsewhere for
# pacing/regression, before trusting this the way EXE_CONFIRMED bytes are.
# --------------------------------------------------------------------------
WALK_TICK_HOOK   = 0x009D6900
WALK_TICK_ORIG   = 0xD101C3FF          # sub sp, sp, #0x70
WALK_X_HOOK      = 0x009DD598
WALK_X_ORIG      = 0x13087D1C          # asr w28, w8, #8
WALK_Y_HOOK      = 0x009DD60C
WALK_Y_ORIG      = 0x13087D1B          # asr w27, w8, #8
WALK_TICK_OFF    = 0x2000              # offset from base_ctr; far past every
                                        # other feature's small allocation
                                        # (FLAG_BASE/THROTTLE_BASE/CAM_FNS/
                                        # ANALOG_GROW all live well under
                                        # 0x400 bytes past base_ctr) so this
                                        # is always safe regardless of which
                                        # other groups a build enables.

# --------------------------------------------------------------------------
# Player vs. NPC/scripted split.
#
# field_update_single_model_position (0x9DC6F0) has exactly two callers
# inside field_update_models_positions, confirmed by scanning .text for
# every BL that targets it:
#   0x9D81E8  the player's own model, driven by analog/stick input every
#             tick with no scripted arrival check -- it just stops when
#             input stops or collision blocks it.
#   0x9D8590  the general loop over every other model (NPCs, and Cloud
#             himself when a MOVE opcode is puppeting him in a cutscene).
#
# The tick-gate above (WALK_X/Y_HOOK) is exactly the right fix for the
# second call site -- 0x9D8590's moves have a sign-flip arrival check that
# only converges cleanly at the stock step size, and gating cadence
# instead of size preserves that step size exactly. But both call sites
# share the SAME hooked function, so gating unconditionally also gated the
# player's own movement -- which has no arrival check to protect and
# instead just needs a smooth value every tick. Position updating only on
# even ticks, rendered every tick, is what the reported flicker/vibration
# is: two different real positions (and, at a wall, two different
# collision-clamped resting positions) alternating at 30Hz against a 60Hz
# camera.
#
# The fix: a one-word flag in the same BSS scratch region, set to 1 for
# the exact duration of the player's own call (two tiny caves bracketing
# 0x9D81E8, touching only x16/x17, replaying their displaced instruction
# first exactly like every other cave here) and left 0 the rest of the
# time. Inside the walk-step hooks, when the flag is set, skip the gate
# entirely and instead run FFNx's own player-path technique -- half speed,
# every tick, computed with correct round-to-nearest-toward-zero so it can
# never truncate to a stuck zero -- verified by exhaustive comparison
# against a from-scratch reference across +/-2,000,000 with zero
# mismatches and zero stuck cases. When the flag is clear, behavior is
# byte-for-byte the confirmed-working tick-gate from before.
# --------------------------------------------------------------------------
WALK_PFLAG_SET_HOOK = 0x009D81E0
WALK_PFLAG_SET_ORIG = 0x51001108       # sub w8, w8, #4
WALK_PFLAG_CLR_HOOK = 0x009D81EC
WALK_PFLAG_CLR_ORIG = 0x29422728       # ldp w8, w9, [x25, #0x10]
WALK_PFLAG_OFF      = 0x2004           # right after the tick counter's 4 bytes


# --------------------------------------------------------------------------
# Battle attack movement -- battle_move_character_sub_426F58's ARM64 body
# (module offset 0xC3D90-0xC4200, 1136 bytes, mapped through the recompilation
# table from stock x86 0x426F58 -- confirmed via nxmap.Main.extent()).
#
# FFNx's PC replacement of this function divides every one of its three
# per-tick position deltas (X, Z, and a keyframed Y lookup) by
# battle_frame_multiplier before adding them, and divides the lookup TABLE
# INDEX itself by the same multiplier so the same handful of table entries
# gets resampled at the original per-tick rate despite the function now
# being called battle_frame_multiplier times as often. Its n_frames is
# already scaled the same way by EFFECT10_CASES (the effect10-scale group,
# above) -- these caves are meaningless without that scaling active, so they
# are gated on the same 'effect10' dispatch tag rather than a separate flag,
# which makes the two impossible to enable out of step with each other.
#
# Without this, any attack using this specific move function overshoots by
# battle_frame_multiplier x -- confirmed on hardware: Aerith's basic attack,
# and enemies with a dash/lunge attack, visibly travel too far, while
# Cloud/Tifa/Barret's attacks (sibling functions 426A26/42739D/4270DE/etc.)
# do not, because those only need their SETUP-time constants scaled, not an
# ongoing per-tick divide -- see EFFECT10_CASES.
#
# Three hook sites share one shape: `add w8, w9, w8`, where w9 is the
# current coordinate and w8 is the raw (correctly bit-patterned, not yet
# sign-extended) 16-bit delta just read from effect10_array_data or
# resting_Y_array_data. A fourth hook scales the lookup INDEX itself
# (field_18, a monotonically non-negative counter, so a plain shift
# suffices -- no truncate-toward-zero bias needed, unlike the three signed
# deltas). All four addresses and original words were re-confirmed fresh
# against the real dumped NSO this session via nxmap + capstone, not taken
# from an earlier session's notes.
#
# w16/w17 are free scratch for the ENTIRE function -- confirmed by grepping
# the full 284-instruction disassembly, neither register is referenced
# anywhere in it -- so unlike the walk-gate caves, nothing here needs BSS
# state: every cave is pure stateless arithmetic on registers already live
# at its hook.
# --------------------------------------------------------------------------
BATTLE_MOVE_X_HOOK    = 0x000C3F58
BATTLE_MOVE_X_ORIG    = 0x0B080128      # add w8, w9, w8   (modelPosition.x += field_C)
BATTLE_MOVE_Z_HOOK    = 0x000C3FF4
BATTLE_MOVE_Z_ORIG    = 0x0B080128      # add w8, w9, w8   (modelPosition.z += field_E)
BATTLE_MOVE_Y_HOOK    = 0x000C40D0
BATTLE_MOVE_Y_ORIG    = 0x0B080128      # add w8, w9, w8   (modelPosition.y += resting_Y[idx])
BATTLE_MOVE_YIDX_HOOK = 0x000C40B4
BATTLE_MOVE_YIDX_ORIG = 0x0B080528      # add w8, w9, w8, lsl #1   (resting_Y index)


def _battle_move_delta_cave(cave, hook, shift):
    """X / Z / Y-lookup delta site. w8 = raw 16-bit delta (zero-extended by
    the ldrh that fed it), w9 = current coordinate. Sign-extends w8, then
    applies the same truncate-toward-zero divide-by-2**shift bias trick
    used for the field walk fix (add (2**shift - 1) when negative, then
    arithmetic shift) -- this is bit-exact with FFNx's C `field_C /
    battle_frame_multiplier` on a signed short, not just an approximation
    of it."""
    mask = (1 << shift) - 1
    w = [
        _lsl(17, 8, 16),          # 0: w17 = raw << 16
        _asr(17, 17, 16),         # 1: w17 = sign-extended raw (32-bit)
        _asr(16, 17, 31),         # 2: w16 = sign mask (all-1s if negative)
        _and_imm(16, 16, mask),   # 3: w16 = bias (mask if negative, else 0)
        _add_reg(17, 17, 16),     # 4: w17 = raw + bias
        _asr(17, 17, shift),      # 5: w17 = trunc(raw / 2**shift)
        _add_reg(8, 9, 17),       # 6: w8 = w9 + w17  (replaces stock add w8,w9,w8)
        0,                        # 7: b hook+4
    ]
    w[7] = _b(cave + 4 * 7, hook + 4)
    return w


def _battle_move_yidx_cave(cave, hook, shift):
    """resting_Y_array_data index site: stock computes
    field_10*16 + field_18*2 (w9 + w8<<1, w9=field_10*16 untouched,
    w8=raw field_18). field_18 only ever counts upward from 0, so a plain
    arithmetic shift exactly matches FFNx's `field_18 / battle_frame_
    multiplier` with no truncation bias needed."""
    w = [
        _asr(17, 8, shift),       # 0: w17 = field_18 / 2**shift (always >= 0)
        _lsl(17, 17, 1),          # 1: w17 = (field_18/2**shift) * 2
        _add_reg(8, 9, 17),       # 2: w8 = w9 + w17  (replaces stock add w8,w9,w8,lsl#1)
        0,                        # 3: b hook+4
    ]
    w[3] = _b(cave + 4 * 3, hook + 4)
    return w


def build_battle_move_caves(text, ro_base, verify_only, log, shift):
    """Dedicated build step, same shape as build_walk_gate_caves but with no
    BSS allocation -- every cave here is stateless. Called from patch_nso
    only when the 'effect10' dispatch tag is active (see comment block
    above)."""
    sites = [
        ('battle attack movement, delta (X)',
         BATTLE_MOVE_X_HOOK, BATTLE_MOVE_X_ORIG,
         lambda cave: _battle_move_delta_cave(cave, BATTLE_MOVE_X_HOOK, shift)),
        ('battle attack movement, delta (Z)',
         BATTLE_MOVE_Z_HOOK, BATTLE_MOVE_Z_ORIG,
         lambda cave: _battle_move_delta_cave(cave, BATTLE_MOVE_Z_HOOK, shift)),
        ('battle attack movement, delta (Y)',
         BATTLE_MOVE_Y_HOOK, BATTLE_MOVE_Y_ORIG,
         lambda cave: _battle_move_delta_cave(cave, BATTLE_MOVE_Y_HOOK, shift)),
        ('battle attack movement, Y-lookup index',
         BATTLE_MOVE_YIDX_HOOK, BATTLE_MOVE_YIDX_ORIG,
         lambda cave: _battle_move_yidx_cave(cave, BATTLE_MOVE_YIDX_HOOK, shift)),
    ]
    for label, hook, orig, build in sites:
        cur, = struct.unpack('<I', text[hook:hook + 4])
        if cur != orig:
            raise SystemExit(
                'ABORT  %s\n       hook offset 0x%X\n'
                '       expected %08X, found %08X -- input is not stock?'
                % (label, hook, orig, cur))
        if verify_only:
            log('  ok  +0x%06X  cave hook            %s' % (hook, label))
            continue
        cave = len(text)
        words = build(cave)
        text.extend(struct.pack('<%dI' % len(words), *words))
        if len(text) > ro_base:
            raise SystemExit('ABORT  battle-move cave overflows .rodata')
        struct.pack_into('<I', text, hook,
                         0x14000000 | (((cave - hook) >> 2) & 0x3FFFFFF))
        log('  ok  +0x%06X  -> cave 0x%06X (%d words)  %s'
            % (hook, cave, len(words), label))


# Raw pre-bias product recovery, specific to each hook's register state at
# the moment the cave takes over (both re-confirmed against the real
# disassembly this session):
#   X (0x9DD598): w8 = biased256 (raw+bias256), w9 = bias256, both intact
#                 since word 0 (the replayed native asr) only reads w8.
#                 raw = w8 - w9.
#   Y (0x9DD60C): w9 already holds the untouched raw product (the mul at
#                 0x9DD5F8 writes it there and nothing after touches it
#                 before the hook). raw = w9, i.e. a plain mov.
WALK_X_RAW_RECOVER = _sub_reg(17, 8, 9)    # w17 = w8 - w9
WALK_Y_RAW_RECOVER = _add_reg(17, 9, 31)   # w17 = w9 + wzr  (mov w17, w9)


def _walk_tick_incr_cave(cave, counter, hook):
    """Runs once per real field tick. Replays the displaced prologue
    instruction untouched, increments the shared tick counter by 1, and
    branches back. Touches only x16/x17 (AAPCS intra-procedure scratch,
    the same registers every other cave in this file relies on being free
    at any point) -- sp, x19-x30 are exactly as stock left them."""
    page, off = counter & ~0xFFF, counter & 0xFFF
    w = [
        WALK_TICK_ORIG,                          # 0: sub sp, sp, #0x70 (replayed)
        _adrp(16, cave + 4, page),                # 1: adrp x16, #page
        _ldr_w(17, 16, off),                      # 2: ldr w17, [x16, #off]
        _add_imm(17, 17, 1),                      # 3: add w17, w17, #1
        _str_w(17, 16, off),                      # 4: str w17, [x16, #off]
        0,                                         # 5: b hook+4
    ]
    w[5] = _b(cave + 4 * 5, hook + 4)
    return w


def _walk_pflag_set_cave(cave, flag_addr, hook):
    """Runs immediately before the player's own call into
    field_update_single_model_position. Replays the displaced instruction
    (sub w8, w8, #4 -- the guest-stack-emulation decrement whose result the
    very next real instruction, str w8,[x25,#0x10], depends on) untouched,
    sets the player flag to 1, and branches back."""
    page, off = flag_addr & ~0xFFF, flag_addr & 0xFFF
    w = [
        WALK_PFLAG_SET_ORIG,                      # 0: sub w8, w8, #4 (replayed)
        _adrp(16, cave + 4, page),                 # 1: adrp x16, #page
        _movz(17, 1),                              # 2: movz w17, #1
        _str_w(17, 16, off),                       # 3: str w17, [x16, #off]
        0,                                          # 4: b hook+4
    ]
    w[4] = _b(cave + 4 * 4, hook + 4)
    return w


def _walk_pflag_clr_cave(cave, flag_addr, hook):
    """Runs immediately after the player's call returns. Replays the
    displaced instruction (ldp w8, w9, [x25, #0x10] -- both halves feed the
    very next real instructions) untouched, clears the player flag back to
    0, and branches back."""
    page, off = flag_addr & ~0xFFF, flag_addr & 0xFFF
    w = [
        WALK_PFLAG_CLR_ORIG,                      # 0: ldp w8, w9, [x25, #0x10] (replayed)
        _adrp(16, cave + 4, page),                 # 1: adrp x16, #page
        _str_w(31, 16, off),                       # 2: str wzr, [x16, #off]
        0,                                          # 3: b hook+4
    ]
    w[3] = _b(cave + 4 * 3, hook + 4)
    return w


def _walk_gate_cave(D, orig, recover, cave, counter, flag_addr, hook):
    """Word 0 always replays the real stock >>8 computation untouched
    (bit-identical to native math; its invariant reads of w8/w9 must stay
    correct for downstream stores either way, and it IS the final result on
    the NPC path).

    Then: if the player-movement flag is set, discard that native value and
    instead compute a continuous, every-tick half-speed result from the
    recovered raw pre-bias product -- FFNx's own technique for the player
    path, no gating, so continuous stick input is smooth and a wall-stop
    resolves to one consistent position instead of alternating.

    If the flag is clear (NPC/scripted), fall back to the tick-gate that
    fixed the southmk2 freeze: keep the native value on even ticks, zero it
    on odd ticks."""
    fpage, foff = flag_addr & ~0xFFF, flag_addr & 0xFFF
    cpage, coff = counter & ~0xFFF, counter & 0xFFF
    w = [
        orig,                                     # 0: wD = w8 asr 8 (native, untouched)
        recover,                                  # 1: w17 = recovered raw product
        _adrp(16, cave + 4 * 2, fpage),            # 2: adrp x16, #flag page
        _ldr_w(16, 16, foff),                      # 3: w16 = player flag
        0,                                         # 4: tbz w16,#0 -> NPC path (idx 10)
        _asr(16, 17, 31),                          # 5: w16 = sign(raw) mask
        _and_imm(16, 16, 0x1ff),                   # 6: w16 = bias511 (0 or 0x1ff)
        _add_reg(16, 17, 16),                      # 7: w16 = raw + bias511
        _asr(D, 16, 9),                            # 8: wD = biased >> 9 (player result)
        0,                                         # 9: b DONE (idx 14)
        _adrp(16, cave + 4 * 10, cpage),           # 10: adrp x16, #counter page
        _ldr_w(16, 16, coff),                      # 11: w16 = tick counter
        0,                                         # 12: tbz w16,#0 -> DONE (even: keep native)
        0x52800000 | (D & 0x1F),                   # 13: movz wD, #0 (odd: zero it)
        0,                                         # 14: b hook+4
    ]
    w[4]  = _tbz(16, 0, cave + 4 * 4, cave + 4 * 10)
    w[9]  = _b(cave + 4 * 9, cave + 4 * 14)
    w[12] = _tbz(16, 0, cave + 4 * 12, cave + 4 * 14)
    w[14] = _b(cave + 4 * 14, hook + 4)
    return w


def build_walk_gate_caves(text, ro_base, segs, data, verify_only, log):
    """Dedicated build step, same shape as the --cam-throttle/--cam-nframes
    blocks in patch_nso: not routed through the generic NSO_CAVES loop
    because these caves need to know their own final address (for ADRP),
    which that loop's ORIG/BACK-only sentinel resolution does not support.
    Always applied (unconditional, like the walk-step patch has been since
    the very first build in this project)."""
    global _BSS_GROW
    bss = struct.unpack('<I', data[0x3C:0x40])[0]
    data_end = (segs[2][1] + segs[2][2] + 0xFFF) & ~0xFFF
    base_ctr = data_end + bss
    counter = base_ctr + WALK_TICK_OFF
    flag_addr = base_ctr + WALK_PFLAG_OFF

    sites = [
        ('field tick counter', WALK_TICK_HOOK, WALK_TICK_ORIG,
         lambda cave: _walk_tick_incr_cave(cave, counter, WALK_TICK_HOOK)),
        ('field walk/run step, player-flag set', WALK_PFLAG_SET_HOOK, WALK_PFLAG_SET_ORIG,
         lambda cave: _walk_pflag_set_cave(cave, flag_addr, WALK_PFLAG_SET_HOOK)),
        ('field walk/run step, player-flag clear', WALK_PFLAG_CLR_HOOK, WALK_PFLAG_CLR_ORIG,
         lambda cave: _walk_pflag_clr_cave(cave, flag_addr, WALK_PFLAG_CLR_HOOK)),
        ('field walk/run step, gate (X)', WALK_X_HOOK, WALK_X_ORIG,
         lambda cave: _walk_gate_cave(28, WALK_X_ORIG, WALK_X_RAW_RECOVER,
                                       cave, counter, flag_addr, WALK_X_HOOK)),
        ('field walk/run step, gate (Y)', WALK_Y_HOOK, WALK_Y_ORIG,
         lambda cave: _walk_gate_cave(27, WALK_Y_ORIG, WALK_Y_RAW_RECOVER,
                                       cave, counter, flag_addr, WALK_Y_HOOK)),
    ]
    for label, hook, orig, build in sites:
        cur, = struct.unpack('<I', text[hook:hook + 4])
        if cur != orig:
            raise SystemExit(
                'ABORT  %s\n       hook offset 0x%X\n'
                '       expected %08X, found %08X -- input is not stock?'
                % (label, hook, orig, cur))
        if verify_only:
            log('  ok  +0x%06X  cave hook            %s' % (hook, label))
            continue
        cave = len(text)
        words = build(cave)
        text.extend(struct.pack('<%dI' % len(words), *words))
        if len(text) > ro_base:
            raise SystemExit('ABORT  walk-gate cave overflows .rodata')
        struct.pack_into('<I', text, hook,
                         0x14000000 | (((cave - hook) >> 2) & 0x3FFFFFF))
        log('  ok  +0x%06X  -> cave 0x%06X (%d words)  %s'
            % (hook, cave, len(words), label))
    _BSS_GROW = max(_BSS_GROW, WALK_PFLAG_OFF + 4)
    log('      walk-tick counter at module +0x%X, player flag at +0x%X, '
        'bssSize grown by >= 0x%X'
        % (counter, flag_addr, WALK_PFLAG_OFF + 4))


def opcode_scaler_cave(cave, site, mult, shift):
    """
    Words for one post-call scaler. `cave` is where they will be placed.

        ldrsh w16, [ctx]        ; w16 = (int16_t) guest AX  -- the return value
        <scale w16>
        strh  w16, [ctx]        ; write it back, truncated to 16 bits as C does
        ldr   w8,  [ctx, #0x10] ; the displaced instruction, replayed verbatim
        b     hook + 4

    multiply: a single LSL, because the multiplier is a power of two.
    divide:   FFNx only divides when abs(ret) >= multiplier, and C truncates
              toward zero, so we reproduce both -- the guard and the bias.
    """
    ctx = site['ctx']
    w = [_ldrsh(TMP, ctx)]
    if site['op'] == 'mul':
        w.append(_lsl(TMP, TMP, shift))
    else:
        # if (ret >= mult) goto div
        # if (ret + mult <= 0) goto div      ; i.e. ret <= -mult
        # goto store
        # div: bias negatives by (mult-1) so the shift truncates toward zero
        base = len(w)
        w += [_subs_imm(31, TMP, mult),          # cmp w16, #mult
              0,                                 # b.ge div
              _adds_imm(31, TMP, mult),          # cmn w16, #mult
              0,                                 # b.le div
              0,                                 # b store
              _add_imm(TMP2, TMP, mult - 1),     # w17 = w16 + mult-1
              _subs_imm(31, TMP, 0),             # cmp w16, #0
              _csel(TMP, TMP2, TMP, COND_LT),    # if <0 use the biased value
              _asr(TMP, TMP, shift)]
        div = cave + 4 * (base + 5)
        store = cave + 4 * (base + 9)
        w[base + 1] = _bcond(cave + 4 * (base + 1), div, COND_GE)
        w[base + 3] = _bcond(cave + 4 * (base + 3), div, COND_LE)
        w[base + 4] = _b(cave + 4 * (base + 4), store)
    w.append(_strh(TMP, ctx))
    w.append(site['displaced'])
    w.append(_b(cave + 4 * (len(w)), site['hook'] + 4))
    return w


# The seven multiply sites. NFADE is separate: it is a divide, and it belongs
# with the field-fade constants it was always meant to accompany.
OPCODE_MUL_NAMES = ('JUMP', 'SCRLA', 'SCR2DC', 'SCR2DL', 'SCRLP', 'OFST',
                    'VWOFT')
OPCODE_DIV_NAMES = ('NFADE',)

# --------------------------------------------------------------------------
# WHY EACH OPCODE IS ITS OWN GROUP
#
# `opcode-scale` used to be all seven or nothing, and that turned out to be
# the wrong granularity in both directions at once: with the group ON the
# camera was mis-framed inside buildings, with it OFF elevators and other
# scripted scrolls ran at double speed. Both are true, because the group
# contains sites that do different things to different subsystems.
#
# Six of the seven multiply a FRAME COUNT into a 16-bit field. The worst a
# wrong multiplier can do there is change how long a move takes; the move
# still ends where the script said.
#
#   JUMP   [model+0x30]  word    jump arc duration
#   SCRLA  [global+0x20] word    scripted scroll -- THIS IS ELEVATOR SPEED
#   SCRLP  [global+0x20] word    scroll to party
#   SCR2DC [global+0x20] word    2D scroll, constant speed
#   SCR2DL [global+0x20] word    2D scroll, linear
#   OFST   [model+0x58]  word    model offset movement
#
# VWOFT is the odd one out, twice over. It is the VIEW OFFSET opcode -- it is
# the one that moves where the camera sits relative to the field -- and its
# result is stored to a BYTE:
#
#   61CB24  push 4 / push 2
#   61CB28  call get_bank_value        <- the site FFNx patches, and we hook
#   61CB30  mov edx, [0xcbf9d8]
#   61CB36  mov byte ptr [edx+0x12], al    <- 8 bits, not 16
#
# So it is the only one of the seven where doubling can produce a WRONG VALUE
# rather than a slower animation: any operand above 127 wraps in `al`. FFNx
# has the same truncation, but FFNx also replaces the field renderer and the
# background/world-coordinate path around it, so a wrapped view offset lands
# somewhere its own code understands. Ours does not.
#
# Each site is therefore selectable on its own. `opcode-scale` still means
# all seven, so existing commands keep working, and `opcode-scale-safe` means
# "the six frame-count sites" -- everything the group was buying you, without
# the one that can move the camera.
SCALER_GROUPS = {
    'opcode-scale': OPCODE_MUL_NAMES,
    'opcode-scale-safe': tuple(n for n in OPCODE_MUL_NAMES if n != 'VWOFT'),
    'nfade': OPCODE_DIV_NAMES,
}
# One group per site, named `scale-<opcode>` in lower case.
for _n in OPCODE_MUL_NAMES:
    SCALER_GROUPS['scale-' + _n.lower()] = (_n,)
for _g in SCALER_GROUPS:
    NSO_GATED.setdefault(_g, [])

# Enabling `opcode-scale` and `scale-vwoft` together must not emit the cave
# twice -- the second would overwrite the first's branch with a branch to a
# cave that returns into the middle of the first. patch_nso already refuses
# duplicate hooks, but the site list is deduplicated here so the ordinary
# case (an umbrella group plus one extra site) just works.
def opcode_scaler_group(names):
    seen, out = set(), []
    for s in OPCODE_SITES:
        if s['name'] in names and s['name'] not in seen:
            seen.add(s['name'])
            out.append(s)
    return out


# --------------------------------------------------------------------------
# Battle effect / camera dispatcher first-frame scalers
#   --enable effect10-scale / effect100-scale / camera-scale
#
# This is the piece the last four sessions kept circling. Every remaining
# battle symptom lives in the four slot dispatchers FFNx replaces outright, and
# none of them is reachable by rewriting a constant, because the value to scale
# does not exist until a slot is registered at runtime.
#
# FFNx's replacement of three of the four adds exactly one thing to the stock
# body: on a slot's FIRST frame after registration, rescale that slot's timing
# fields, keyed on which function was registered. That is a cave, not a
# reimplementation:
#
#   add_fn_to_*      hook the `array_fn[idx] = function` store; set flag[idx]
#   execute_*        hook the `cmp array_fn[idx], #0` slot guard; if flag[idx],
#                    clear it and rescale array_data[idx]
#
# The flag array replaces FFNx's per-slot AuxiliaryEffectHandler. Setting it at
# REGISTRATION rather than inferring "first frame" from the slot contents is
# what makes it correct: a slot can be freed and re-registered with the same
# function in the same frame, and any scheme that watches array_fn for a
# transition misses exactly that case -- which is the common one for repeated
# attacks.
#
# What this does NOT do: execute_effect60_fn. Its first-frame block does not
# only rescale -- for every function it does not name it installs an
# InterpolationEffectDecorator that runs the slot function at the original rate
# and interpolates rotation matrices, palettes and colours between frames. That
# is per-slot C++ state and real behaviour, not arithmetic, and it is honestly
# out of reach of a cave. See DISPATCH_NOTES.md.
#
# Sites come from ff7nx_locate.py -- derived through FFNx's chain, mapped
# through 0x126D3A8, and matched by exact instruction signature. Each site
# carries the surrounding stock words it was identified by, and every one of
# them is re-verified below before anything is written.
# --------------------------------------------------------------------------
try:
    import ff7nx_dispatch as _disp
    import ff7nx_shared_prologue as _sharedp
    from ff7nx_dispatch_sites import (DISPATCH_SITES, FLAG_BASE, BSS_GROW,
                                      THROTTLE_BASE, BSS_GROW_THROTTLE,
                                      PAUSED_GUEST, CAMERA_WAIT_SITES)
    try:
        from ff7nx_dispatch_sites import FIELD_WAIT_SITES
    except ImportError:                       # older generated sites file
        FIELD_WAIT_SITES = {}
    try:
        from ff7nx_dispatch_sites import FIELD_BLINK_SITES
    except ImportError:
        FIELD_BLINK_SITES = {}
    EFFECT60_SLOTS = _disp.EFFECT60_SLOTS
except ImportError:
    _disp = None
    _sharedp = None
    DISPATCH_SITES, FLAG_BASE, BSS_GROW = {}, 0, 0
    THROTTLE_BASE, BSS_GROW_THROTTLE, PAUSED_GUEST = 0, 0, 0
    CAMERA_WAIT_SITES = {}
    FIELD_WAIT_SITES = {}
    FIELD_BLINK_SITES = {}
    EFFECT60_SLOTS = []

NEW_UNTESTED = ('victory', 'damage-numbers', 'limit-aura',
                'victory-fade', 'aura-eskill', 'aura-summon',
                'boss-death', 'battle-text',
                # constants only, no injected code, but never run on hardware
                'field-text', 'field-blink', 'tifa-slots')

DISPATCH_GROUPS = {
    'effect10-scale': 'effect10',
    'effect100-scale': 'effect100',
    'camera-scale': 'camera',
}

# --------------------------------------------------------------------------
# `*-throttle` -- the pause-throttle decorator
#
# The first-frame `*-scale` groups reproduce FFNx's ARITHMETIC arms. They are
# not the whole dispatcher: every effect100 and effect60 slot function FFNx does
# not name gets an InterpolationEffectDecorator instead, whose pacing -- strip
# the smoothing away and all three Interpolation classes are the same -- is
#
#     if (frameCounter % 4 == 0)  fn();
#     else { wasPaused = *paused; *paused = 1; fn(); *paused = wasPaused; }
#     frameCounter++;
#
# Without it those functions run four times too fast, which is exactly what is
# left broken: limit-break, magic and summon cameras race (they are effect100
# slot functions, not `execute_camera_functions` entries -- that is why
# camera-scale fixed the battle intro camera and nothing else), and limit-break
# damage lands before the animation finishes (the animation comes from
# battle.lgp and IS scaled x4; the effect100 function that decides when damage
# applies is not).
#
# Three caves per dispatcher: the registration hook seeds the per-slot byte, the
# pre-call hook sets the pause, the post-call hook takes it away. See
# ff7nx_dispatch.py for the register argument and for why the call is never
# skipped.
#
# These are ALLOW-BY-DEFAULT -- every slot function is throttled unless it is on
# FFNx's exclusion list. That is what FFNx does, and it is the single biggest
# risk in this change, so they are separate groups, excluded from --enable-all,
# and meant to be turned on ONE AT A TIME with `--throttle-exclude` available to
# bisect the list.
THROTTLE_GROUPS = {
    'effect100-throttle': 'effect100',
    'effect60-throttle': 'effect60',
    # Same three caves as effect60-throttle, but the registration table is an
    # ALLOW list rather than an exclusion list: it throttles only the aura
    # spawner and the magic aura handler. `effect60-throttle` throttles all 60
    # slots, which reproduces FFNx's rule but not its interpolation, and
    # effect60 is the per-frame visual layer -- the result is the whole battle
    # visibly stepping at 15 Hz. This is the same fix with the blast radius cut
    # to the two functions that actually need it.
    'aura-throttle': 'effect60',
}
THROTTLE_ALLOW_GROUPS = {'aura-throttle'}


# --------------------------------------------------------------------------
# `camera-wait` -- the battle camera SCRIPT pacing
#
# The pause-throttle fixed limit-break damage timing, which proved the effect100
# mechanism. It did not fix the limit-break and magic CAMERAS, because those are
# not paced by a slot function at all -- they are paced by a bytecode script.
#
# FF7 drives every battle camera from a script: the camdat archives for attacks,
# magic, limits and summons, plus two tables inside ff7_en for the battle intro.
# `set_camera_position_scripts` and `set_camera_focal_position_scripts` step one
# of those scripts once per frame. Opcode 0xF5 is "wait N frames"; opcode 0xF4
# ticks the counter down. At 60 FPS the interpreter runs four times as often, so
# every wait expires four times too early and the camera races -- which is
# exactly the symptom, and nothing in the effect dispatchers could ever have
# reached it.
#
# FFNx scales those waits in camera.cpp by re-simulating the script around each
# call. We do the same arithmetic at the source instead: one cave on the single
# store each interpreter makes for opcode 0xF5. Two caves, ten words each.
#
# Why not `--camdat`, which already exists here? Because `frames_to_wait` is a
# short but the camdat operand is a byte. Of the 8,213 wait operands in
# camdat0/1/2, 166 are above 0x3F -- multiplied by four they do not fit in a
# byte and a static patch clamps every one of them at 255. The cave writes the
# 16-bit field, so 254*4 = 1016 is fine. It also covers the battle-intro scripts
# that live in ff7_en and are not in the camdat files at all. Run
# scan_camdat_waits.py for the numbers on your own archives.
CAMERA_WAIT_GROUP = 'camera-wait'


# --------------------------------------------------------------------------
# `field-wait` -- the FIELD script pacing
#
# The same mistake as camera-wait, one module over, and the one with the
# widest blast radius of anything still missing.
#
# `ff7_en`'s field limiter divisor went 30 -> 60, so `field_loop` runs twice
# per original frame -- and it steps the field opcode interpreter every time.
# Opcode 0x24 (WAIT) latches a frame count and counts it down once per step,
# so EVERY scripted pause in the game expires in half the time. Anything a
# field script paces by hand runs at double speed: the train's warning lights
# and the SFX cued between them, alarms, elevator sequences, timed NPC
# business. It is not the animation system and it is not the background
# system; those are correct. It is the script's own clock.
#
# FFNx fixes it in `opcode_script_WAIT` by multiplying the latched value by
# `common_frame_multiplier`. We do the same arithmetic at the single store the
# stock handler makes, with one cave. See `build_field_wait_cave`.
#
# This is deliberately NOT in --enable-all. It injects code, it has no
# hardware history, and it changes the pacing of every scripted scene in the
# game -- which is exactly why it must be tested by itself.
FIELD_WAIT_GROUP = 'field-wait'


# --------------------------------------------------------------------------
# `field-blink-hold` -- how long the eyes stay shut
#
# The companion to the `field-blink` constants, and the half of the problem
# constants cannot reach. `field-blink` fixes how OFTEN models blink;
# this fixes how LONG each blink lasts.
#
# The state machine sets "eyes shut" and reloads the interval counter in the
# same arm, so mode 2 survives exactly one frame no matter what the counter
# says. At 30 FPS that is 33 ms; at 60 FPS it is 16 ms, which is why the
# blinks read as a flicker even once the interval is right.
#
# Two caves. The test is widened from `counter == 0` to `counter <= 0`, and
# the reload stores -1 the first time and the real interval the second, so the
# shut arm runs on two consecutive frames. See build_field_blink_test_cave and
# build_field_blink_hold_cave for why neither can be a rewritten instruction.
#
# Composes with `field-blink` without adjustment: the extra frame is already
# accounted for in the cycle arithmetic (reload + 2 at 60 FPS against
# reload + 1 at 30).
FIELD_BLINK_GROUP = 'field-blink-hold'

for _g in (list(DISPATCH_GROUPS) + list(THROTTLE_GROUPS)
           + [CAMERA_WAIT_GROUP, FIELD_WAIT_GROUP, FIELD_BLINK_GROUP]):
    NSO_GATED.setdefault(_g, [])


# --------------------------------------------------------------------------
# QUARANTINE: FFNx patches that are not framerate patches at all
#
# ff7nx_resolve.py scrapes `patch_*_code` specs out of FFNx's sources with a
# regex. The regex has no idea what `if` a spec sits inside, and several of
# the specs in the files it reads are NOT part of the 60 FPS work:
#
#   field_init_viewport_values +0x35/+0x6E   FFNx's "field vertical center"
#                                            option, gated on
#                                            `ff7_field_center || widescreen`.
#                                            It moves the field viewport's
#                                            origin and height -- i.e. it
#                                            changes the framing of every
#                                            field screen in the game.
#   field_draw_everything +0xE2/+0x353       paired with FFNx's replaced
#                                            field_layer*_pick_tiles.
#   kernel_load_kernel2 +0x1D                kernel2 buffer size.
#   coaster_sub_5EE150 +0x129..+0x190        "coaster aim fix".
#   world_sub_75C283 +0x2A8                  per-language literal in a
#                                            `switch (version)`.
#   highway_exit_address_location            highway exit bugfix.
#   world_submit_draw_clouds_and_meteor,     all inside FFNx's
#   world_init_load_map_meshes_...,          `enable_worldmap_external_mesh`
#   world_wm{0,2,3}_*_draw_all               renderer replacement.
#
# None of these are currently reachable from --enable-all (they all landed in
# p-*/queued groups). That is luck, not design: `field_init_viewport_values`
# is one `--enable p-battle_misc` away, and it is precisely a "the field is
# framed wrong / zoomed wrong" patch. Strip them at load so no future
# regeneration or bisect command can turn one on by accident.
NON_FPS_SYMBOLS = (
    'field_init_viewport_values',
    'field_draw_everything',
    'kernel_load_kernel2',
    'coaster_sub_5EE150',
    'world_sub_75C283',
    'highway_exit_address_location',
    'world_submit_draw_clouds_and_meteor_7547A6',
    'world_init_load_map_meshes_graphics_objects_75A283',
    'world_wm0_overworld_draw_all_74C179',
    'world_wm2_underwater_draw_all_74C3F0',
    'world_wm3_snowstorm_draw_all_74C589',
)


def _quarantine_non_fps(tables):
    """
    Drop every patch whose label names a NON_FPS_SYMBOLS function.

    A group that the sweep empties is removed outright, so it stops appearing
    in --list-groups and stops being a valid --enable name. A group that was
    ALREADY empty is left alone: the cave-only groups (opcode-scale, nfade,
    the dispatchers, camera-wait, field-wait) carry no word patches by design.
    """
    dropped = []
    for table in tables:
        for group in list(table):
            before = len(table[group])
            keep = [p for p in table[group]
                    if not any(p[0].startswith(s) for s in NON_FPS_SYMBOLS)]
            if len(keep) == before:
                continue
            dropped.append((group, before - len(keep)))
            if keep:
                table[group] = keep
            else:
                del table[group]
    return dropped


QUARANTINED = _quarantine_non_fps([NSO_GATED, EXE_GATED])


# --------------------------------------------------------------------------
# MIS-RESOLVED, verified by hand. Removed so it cannot be enabled by name.
#
# The resolver placed `field_text_box_window_paging_631945+0xFD` at module
# +0x9CF7EC. That word is `asr w8, w8, #5`, and it is real -- but it is the
# translation of x86 +0x121, not +0xFD. The function has TWO `sar reg, 5`,
# one in each arm of a branch:
#
#   +0xF9  sub eax, edx      ; 0x80 - speed          <- FFNx patches this arm
#   +0xFB  sar eax, 5        ;   immediate at +0xFD
#   +0xFE  add eax, 2        ;   immediate at +0x100
#
#   +0x11B sub ecx, 0x80     ; speed - 0x80          <- FFNx leaves this alone
#   +0x121 sar ecx, 5
#   +0x124 add ecx, 1
#
# They assemble to the identical ARM64 word, and the recompiler emitted the
# SECOND arm first, so "the first candidate" is the wrong one. Enabling
# p-field_text would have scaled a branch FFNx deliberately does not touch
# while leaving the one it does at stock. The correct sites are in the
# `field-text` group below, anchored on their surrounding instructions rather
# than on the immediate.
MIS_RESOLVED = {
    ('p-field_text', 'field_text_box_window_paging_631945+0xFD', 0x009CF7EC),
}

for _g, _lbl, _off in MIS_RESOLVED:
    if _g in NSO_GATED:
        NSO_GATED[_g] = [p for p in NSO_GATED[_g]
                         if not (p[0] == _lbl and p[1] == _off)]


# --------------------------------------------------------------------------
# `field-text` -- the dialogue window, completed and corrected
#
# FFNx patches four constants in field_text_box_window_paging_631945 and two
# in field_text_box_window_opening_6317A9. Between them they control how fast
# the message box grows, pages and opens.
#
# Neither reached any build:
#
#   * paging landed in `p-field_text` with 2 of 4 constants, one of which was
#     at the wrong address (see MIS_RESOLVED above), so the group was excluded
#     as PARTIAL -- correctly, as it turns out;
#
#   * opening landed in `c-field_text` and was excluded as CODE_PAIRED,
#     because the resolver saw
#
#         replace_function(ff7_externals.field_text_box_window_opening_6317A9,
#                          field_text_box_window_opening_6317A9_jp);
#
#     in ff7_opengl.cpp and concluded FFNx replaces the function. It does --
#     ONLY IN THE JAPANESE BUILD. That call sits in the `if (version ==
#     VERSION_FF7_102_JP)` block alongside the other japanese_text.cpp
#     replacements. On an English build FFNx runs the stock function with the
#     two constants patched, which is exactly what we can do. The scraper has
#     no notion of which `if` a `replace_function` sits inside, the same
#     blindness that put a widescreen viewport patch in p-battle_misc.
#
# So on every build so far the message box has opened and paged at double
# speed while its closing animation was correct. Addresses below were derived
# by matching the x86 instruction SEQUENCE through the translation, not by
# searching for an immediate.
NSO_GATED['field-text'] = [
    # paging: (0x80 - speed) >> 5 + 2, halved -- FFNx +0xFD and +0x100
    ('field_text_box_window_paging_631945+0xFD  step >>5 -> >>6',
     0x009CF81C, 0x13057D08, 0x13067D08),
    ('field_text_box_window_paging_631945+0x100 step +2 -> +1',
     0x009CF820, 0x11000913, 0x11000513),
    # paging: the constant page step, 2 -> 1 -- FFNx +0x111
    ('field_text_box_window_paging_631945+0x111 page step 2 -> 1',
     0x009CF7C8, 0x321F03E8, 0x52800028),
    # paging: the per-line advance, >>4 -> >>5 -- FFNx +0x141
    ('field_text_box_window_paging_631945+0x141 line >>4 -> >>5',
     0x009CF8C8, 0x13047D08, 0x13057D08),
    # opening: the box grow step, halved twice -- FFNx +0x3D and +0xD2
    ('field_text_box_window_opening_6317A9+0x3D  grow >>2 -> >>3',
     0x009CEF4C, 0x13027D08, 0x13037D08),
    ('field_text_box_window_opening_6317A9+0xD2  grow >>2 -> >>3',
     0x009CF164, 0x13027D08, 0x13037D08),
]


# --------------------------------------------------------------------------
# `field-blink` -- field model eye blinking
#
# Not an FFNx patch. FFNx replaces field_blink_3d_model_649B50 outright, but
# its replacement is about CUSTOM EYE TEXTURES (ff7_advanced_blinking) and
# contains no timing at all. Nothing in FFNx scales the blink rate, so there
# was nothing for the resolver to find.
#
# The state machine lives in the caller, field_animate_3d_models_6392BB, at
# x86 +0x7D9:
#
#     counter = blink_frames[model]              ; word at 0xCC167A + id*0x88
#     if (counter == 0) {
#         blink_mode_left = blink_mode_right = 2 ;   eyes shut, ONE frame
#         counter = (jitter_table[i++] & 0x1F) + 0x40    ; +0x814 / +0x817
#     } else {
#         blink_mode_left = blink_mode_right = 1 ;   eyes open
#         counter--
#     }
#
# The caller runs once per field frame. At 60 FPS that is twice as often, so
# the 64..95 frame interval elapses in half the wall-clock time and everyone
# blinks twice as fast. Doubling the reload restores it: 128..191 frames.
#
# The recompiler folded `(x & 0x1F) + 0x40` into an insert, which is why no
# immediate scan could find it -- there is no 0x1F and no 0x40 operand in the
# translated body:
#
#     94B6CC  mov   w9, #0x40           <- the addend
#     94B6D0  bfxil w9, w8, #0, #5      <- the mask
#
# The identity holds because bit 6 is clear after masking, so `+ 0x40` and
# `| 0x40` are the same value.
#
# The fix DOUBLES rather than widens. The first version raised the base to
# 0x80 and widened the field to 6 bits, giving 128..191 -- the right range,
# and the right average, but not the right value for any particular jitter
# byte: at jitter 2 it produced a 134-frame cycle where vanilla would have
# asked for 67 * 2 = 134... and at jitter 1, 131 where vanilla wanted 132.
# Off by up to a 30 FPS frame, per blink. Shifting the inserted field left by
# one instead makes the reload exactly twice the vanilla value:
#
#     bfi w9, w8, #1, #5    ->  0x80 | ((jitter & 0x1F) << 1)
#                           ->  128, 130, ... 190 = 2 x (64..95)
#
# so every cycle is exactly 2x its vanilla length, to the millisecond, for all
# 256 jitter bytes. Verified over the whole range in test_field_blink.py.
# Both instructions are unique in the function.
#
# This group sets how OFTEN the eyes blink. How LONG each blink lasts is
# `field-blink-hold`, which cannot be done with constants -- see there.
NSO_GATED['field-blink'] = [
    ('blink reload base 0x40 -> 0x80 (6392BB+0x817)',
     0x0094B6CC, 0x321A03E9, 0x52801009),
    ('blink reload jitter x2 (bfxil #0,#5 -> bfi #1,#5) (6392BB+0x814)',
     0x0094B6D0, 0x33001109, 0x331F1109),
]


# --------------------------------------------------------------------------
# `victory` -- battle outro / results-screen pacing
#
# FFNx scales four constants in battle_sub_430DD0 (camera.cpp). Three of them
# are `mov dword ptr [0x9AE138], N` -- 30, 8 and 49 -- writing the same wait
# global from three different exit paths. The fourth, `+0x60E`, is
# `cmp dword ptr [0x9AE148], 0x10`.
#
# Why this was missed for four sessions
# -------------------------------------
# Only two of the three stores ever resolved, and they landed in
# `p-battle_camera`, which `--enable-all` EXCLUDES because the group was
# incomplete. So the victory outro constants were never applied in ANY build,
# including every build where victory pacing was tested. The old handoff's claim
# that `r-battle_camera` carried them was simply wrong -- `r-battle_camera` is
# the two ATB constants and nothing else. That is why turning r-battle_camera on
# and off never moved the victory symptom.
#
# The third store (8 -> 32) could not be found by searching for the value: four
# ARM64 sites in this function materialise 8, and the nearest one is a `push 8`
# feeding a call. It was located by anchoring on the STORE TARGET instead --
# "where does this function write 8 to guest 0x9AE138?" has exactly one answer.
# See ff7nx_conststore.py.
#
# `+0x60E` is deliberately absent: guest 0x9AE148 is never formed anywhere in
# this function's ARM64 body, so the compare FFNx patches has no translated
# counterpart and the patch would be a no-op. Recorded rather than silently
# dropped.
NSO_GATED['victory'] = [
    ('battle_sub_430DD0+0x326 outro wait 30 -> 120', 0x0009D668, 0x321F0FE8, 0x52800F08),
    ('battle_sub_430DD0+0x361 outro wait 8 -> 32',   0x0009E550, 0x321D03E8, 0x52800408),
    ('battle_sub_430DD0+0x3DE outro wait 49 -> 196', 0x0009E510, 0x52800628, 0x52801888),
]


# --------------------------------------------------------------------------
# `battle-text` -- how long the text at the top of the screen stays up
#
# Three separate things decide this and none of them was in any build.
#
# 1. THE ACTION STRING ("Fire2", "Braver", "Attack")
#
# `display_battle_action_text_42782A` has its `field_6` scaled by
# `effect100-scale`, so the slot's own countdown was already right. What sets
# that countdown is a different function, and FFNx REPLACES it rather than
# patching a constant, which is why the resolver never saw it:
#
#     int get_n_frames_display_action_string() {
#         int shiftValue = 2 - battle_frame_multiplier / 2;      // 0 at x4
#         return ((int)*field_byte_DC0E11 >> shiftValue)
#                + 4 * battle_frame_multiplier;                  // + 16 at x4
#     }
#
# The stock function at 0x5BE475 is exactly `(byte >> 2) + 4`, so at x4 FFNx
# computes `(byte >> 0) + 16` -- two instructions, not a function replacement:
#
#     asr w8, w8, #2   ->  asr w8, w8, #0
#     add w8, w8, #4   ->  add w8, w8, #16
#
# The result is `4*stock + (byte % 4)`, i.e. exactly four times as long give or
# take three frames of rounding, which is what FFNx settles for too.
#
# Picking the right `add` matters: there are three `add w8,w8,#4` in that
# function and the other two adjust the guest stack pointer. Only +0x7CF284
# writes the result back to guest EAX at `[x19]`; the others write `[x19,#0x10]`.
#
# WHAT IS *NOT* HERE, AND WHY THAT IS THE INTERESTING PART
#
# FFNx's other two text patches -- `battle_sub_434C8B+0x4F` (15 -> 60, a text
# hold) and `battle_sub_435D81+0x6A8` (47 -> 188, the all-lucky-7s text) -- are
# already applied. They resolved cleanly into `r-battle_misc`, which
# `--enable-all` turns on. I nearly shipped them in this group as well; the
# duplicate-offset check in patch_nso caught it and reported them as already
# covered.
#
# So the entire battle-text problem is these two words. Everything else FFNx
# does for battle text was already in the build, which is exactly why the
# symptom was "text is too fast" rather than "text is broken": the slot
# countdown and the two holds were right and only the value they count down
# from was a quarter of what it should be.
NSO_GATED['battle-text'] = [
    ('get_n_frames_display_action_string  (byte >> 2) -> (byte >> 0)',
     0x007CF280, 0x13027D08, 0x13007D08),
    ('get_n_frames_display_action_string  + 4 -> + 16  (the guest-EAX add, not '
     'the two stack adds)',
     0x007CF284, 0x11001108, 0x11004108),
    ('battle dialogue: the INLINED copy in add_text_to_display_queue, '
     '(byte >> 2) -> (byte >> 0)',
     0x007CF188, 0x13027D08, 0x13007D08),
    ('battle dialogue: the INLINED copy in add_text_to_display_queue, '
     '+ 4 -> + 16',
     0x007CF18C, 0x11001108, 0x11004108),
]


# --------------------------------------------------------------------------
# `boss-death` -- the boss vanquish effect
#
# A boss death is a countdown in `battle_boss_death_sub_5BC5EC`, an effect60
# slot FFNx runs at full rate:
#
#     n_frames = 64                       (set by battle_boss_death_5BC48C)
#     each frame:
#         if (n_frames == 0) retire
#         if (n_frames == 58 || n_frames == 64) boss_death_call(0xFA,0xFA,0xFA)
#         modelPosition.z = field_A + ((n_frames & 1) ? +64 : -64)
#         n_frames--
#
# At 60 FPS that whole countdown runs in a quarter of the wall time, which is
# the "vanquish effect happens too fast".
#
# WHY THIS LOOKED LIKE IT NEEDED A CODE CAVE, AND DOES NOT
#
# I previously costed this at 80-110 words plus a table and declined to ship it.
# That was because FFNx does not merely rescale the function -- it REPLACES it,
# and swaps the two-position `(n & 1)` toggle for an eight-entry interpolation
# table {64,32,0,-32,-64,-32,0,32}. Reproducing that needs a cave and 16 bytes
# of table, and there was no room.
#
# But the table is a SMOOTHNESS upgrade, not the fix. The stock wobble is a
# two-position toggle and the only thing wrong with it at 60 FPS is that it
# flips four times too often. `n_frames` now counts four times as far, so the
# stock behaviour is recovered exactly by testing bit 2 instead of bit 0:
#
#     stock    toggles on  c & 1
#     patched  toggles on  (4c) & 4      and  bit2(4c) == bit0(c) for all c
#
# Identical wobble, identical period, one word. What is given up against FFNx is
# the eight-step interpolation -- the boss wobbles between two positions at
# 7.5 Hz the way it always did, rather than through eight at 30 Hz.
#
# The recompiler computes the guest flags from that AND, so it cannot simply
# become `and #4`: `and w8,w19,#1` is followed by `eor w8,w8,#1` to form ZF, and
# a result of 0-or-4 would make ZF wrong. `ubfx w8,w19,#2,#1` extracts bit 2 to
# bit 0, so the value stays 0-or-1 and the flag arithmetic downstream is
# untouched.
#
# The two `== 58` / `== 64` triggers fire the flash at 58/64 and 64/64 of the
# countdown; at 232/256 and 256/256 they fire at the same fractions.
NSO_GATED['boss-death'] = [
    ('battle_boss_death_5BC48C+0x40,+0xCF  n_frames 64 -> 256 (one word, two '
     'x86 sites)',            0x007C9130, 0x321A03F6, 0x52802016),
    ('battle_boss_death_sub_5BC5EC  flash trigger 58 -> 232',
     0x007C9CD0, 0x7100E91F, 0x7103A11F),
    ('battle_boss_death_sub_5BC5EC  flash trigger 64 -> 256',
     0x007C9D08, 0x7101011F, 0x7104011F),
    ('battle_boss_death_sub_5BC5EC  wobble toggles on bit 2, not bit 0 '
     '(and #1 -> ubfx #2,#1 so the guest ZF stays valid)',
     0x007C9DB0, 0x12000268, 0x53020A68),
    ('battle_boss_death_sub_5BC6ED+0xCF  per-frame step 0x20 -> 8',
     0x007C9AAC, 0x11008108, 0x11002108),
    ('battle_boss_death_sub_5BC6ED+0xF1  per-frame step 4 -> 1',
     0x007C9B04, 0x51001108, 0x51000508),
]

# NOT included: battle_disintegrate_1_death_5BBF31+0x40. FFNx pairs it with
# `replace_function(battle_disintegrate_1_death_sub_5BC04D, ...)`, which has not
# been read yet, and scaling the frame count without whatever that replacement
# does is the same mistake this group exists to avoid.


# --------------------------------------------------------------------------
# `victory-fade` -- the post-battle fade to black
#
# THE FOURTH VICTORY PATCH, PREVIOUSLY RECORDED AS UNREACHABLE
#
# `battle_sub_430DD0` ends the battle with a 16-frame counter that gates the
# jump to the results screen:
#
#     mov ecx, [0x9AE148]        ; the state's frame counter
#     add ecx, 1
#     mov [0x9AE148], ecx
#     cmp dword [0x9AE148], 0x10 ; <-- FFNx +0x60E, x4
#     jl  keep_going
#     mov [0x9AE148], 0
#     mov [0x9AE13C], 5          ; state 5 = EXP / gil
#     mov [0x9AB074], 1
#
# The screen fades over that window. The fade itself is already scaled, so it
# now takes four times as many frames -- but this counter still fires at 16, so
# the results screen cuts in a quarter of the way through. The screen goes
# slightly grey and then snaps to the EXP tally. That is the symptom exactly.
#
# `victory` carries the other three `battle_sub_430DD0` constants. This one was
# recorded as "guest 0x9AE148 is never formed anywhere in this function's ARM64
# body, so the compare FFNx patches has no translated counterpart and the patch
# would be a no-op." That was wrong. The address IS formed -- as
# `add w21, w25, #0x2C` where w25 holds 0x9AE11C, a base the recompiler keeps
# live across 1,644 instructions in this function. Searching for a materialised
# 0x9AE148 finds nothing; searching for base+offset finds it immediately.
#
# WHY THIS IS TWO WORDS AND NOT ONE
#
# There is no `cmp` in the ARM64 at all. The recompiler open-codes the x86 flags
# `jl` needs, and the immediate appears TWICE:
#
#     ldrh w8,[x0] ; ldrh w9,[x0,#2] ; bfi w8,w9,#16,#16   ; w8 = the counter
#     mov  w11, #0xF          ; K-1        <-- +0x9E348
#     lsl  w10, w9, #16       ; counter & 0xFFFF0000
#     sub  w9,  w8, #0x10     ; K          <-- +0x9E350
#     sub  w8,  w11, w8       ; (K-1) - counter
#     and  w8,  w8, w10
#     lsr  w9,  w9, #31       ; SF = sign(counter - K)
#     lsr  w8,  w8, #31       ; OF = sign(counter) & sign((K-1) - counter)
#     cmp  w8, w9 ; b.ne      ; jl  ==  SF != OF
#
# The `mov w11, #0xF` is not a coincidence -- it is K-1, and it is what makes
# the overflow flag correct: `a - K` overflows exactly when `a < 0` and
# `(K-1) - a` also goes negative, which is `a < K - 2^31`.
#
# WHICH WORD DOES WHAT -- MEASURED, BECAUSE THE FIRST ANSWER WAS WRONG
#
# +0x9E350 (K) is the decisive one: it alone moves the branch for exactly the 48
# counter values 16..63, which is the fix.
#
# +0x9E348 (K-1) feeds only the overflow flag, and OF is
# `bit31(a) AND bit31((K-1) - a)` -- both bits can be set together only when
# `a < K - 2^31`. This counter starts at 0 and is reset at K, so it is never
# negative and that region is unreachable. The first version of this note
# therefore claimed the word was inert and could be left alone. That was wrong:
# the branch DOES differ at inputs such as 0x8000003F, which a wide enough probe
# in test_victory_fade.py finds immediately.
#
# So: one word is load-bearing for real counter values, the other is load-
# bearing only for values this counter cannot hold. Both are patched, because a
# flag pair is only self-consistent when both halves move, and "unreachable
# today" is not a property this tree should be relying on four instructions
# away from the thing it is trying to fix. The test asserts the reachable
# behaviour exactly and records the unreachable difference rather than
# hand-waving it.
NSO_GATED['victory-fade'] = [
    ('battle_sub_430DD0+0x60E results-screen gate 16 -> 64 frames (the K-1 '
     'half of the flag computation)',
     0x0009E348, 0x32000FEB, 0x320017EB),
    ('battle_sub_430DD0+0x60E results-screen gate 16 -> 64 frames (the K '
     'half)',
     0x0009E350, 0x51004109, 0x51010109),
]


# --------------------------------------------------------------------------
# `limit-aura` -- the limit break charge-up glow
#
# THE SAME TRAP AS `victory`, ONE FUNCTION OVER
#
# `limit_break_aura_effects_5C0572` is the charging aura: the glow that grows
# around the character before a limit break fires. FFNx gives it SEVEN patches
# and they are one calibrated set -- three phase boundaries, two growth rates,
# one shift and one end frame. The resolver could only place four of them, so
# all seven went into `p-battle_aura`, and `--enable-all` excludes every `p-`
# group. So on every build so far, including the one that fixed the damage
# timing, the limit break aura has had ZERO of its seven patches applied and has
# been running its whole lifecycle four times too fast.
#
# That is the "initial charging effect" being out of step with everything else.
#
# WHY THE OTHER THREE WOULD NOT RESOLVE, AND WHY THERE ARE ONLY SIX PATCHES HERE
#
# The recompiler folds. FFNx patches the x86 as seven edits; the ARM64 needs six
# words, because one of the seven has no counterpart:
#
#   FFNx +0x4C   imul ecx,ecx,0x600   ->  ARM64 `add w8,w8,w8,lsl #1` (x3)
#                                          then `lsl w24,w8,#9`   (x3 << 9 = x0x600)
#                divide by 4  ==  lsl #9 -> #7
#   FFNx +0x7A   imul eax,eax,0xC00   ->  x3 then `lsl w24,w8,#10`
#                divide by 4  ==  lsl #10 -> #8
#   FFNx +0xAD   sub ecx,8            ->  FOLDED INTO THE SHIFT. The ARM64 is
#   FFNx +0xB0   shl ecx,9                `lsl w8,w8,#9` then `sub w21,w8,#0x1000`,
#                                         i.e. (x<<9) - 0x1000 rather than
#                                         (x-8)<<9. FFNx's patched form is
#                                         (x-32)<<7 = (x<<7) - 0x1000 -- the
#                                         SAME subtrahend. So only the shift
#                                         changes and +0xAD needs no patch at
#                                         all.
#
# The resolver refused these three because it searches for an immediate equal to
# the stock x86 operand, and after folding there is no 0x600, no 0xC00 and no
# lone 8 to find -- there are two `lsl #9`s and one `lsl #10`. Reading the
# arithmetic is what places them, and the three lsl sites are the ONLY three in
# the entire function body, which is what makes the assignment unambiguous.
#
# The remaining four of FFNx's seven are the three the resolver did place, plus
# the +0xAD that needs nothing.
NSO_GATED['limit-aura'] = [
    ('limit_break_aura_effects_5C0572+0x4C  aura growth 0x600 -> 0x180 '
     '(x3, lsl 9 -> 7)',        0x007D81E4, 0x53175918, 0x53196118),
    ('limit_break_aura_effects_5C0572+0x6E  phase 1 boundary 8 -> 32',
     0x007D8244, 0x7100213F, 0x7100813F),
    ('limit_break_aura_effects_5C0572+0x7A  aura growth 0xC00 -> 0x300 '
     '(x3, lsl 10 -> 8)',       0x007D8300, 0x53165518, 0x53185D18),
    ('limit_break_aura_effects_5C0572+0x98  phase 2 boundary 16 -> 64',
     0x007D8284, 0x7100411F, 0x7101011F),
    ('limit_break_aura_effects_5C0572+0xAD+0xB0  (x-8)<<9 -> (x-32)<<7, one '
     'word: lsl 9 -> 7',        0x007D82D8, 0x53175908, 0x53196108),
    ('limit_break_aura_effects_5C0572+0x13E aura ends at frame 15 -> 60',
     0x007D853C, 0x71003D1F, 0x7100F11F),
]

# --------------------------------------------------------------------------
# `aura-eskill` / `aura-summon` -- the other two aura types
#
# FF7 has FOUR aura types, dispatched by `run_aura_effects_5C0230` through a
# jump table on the aura index:
#
#     type 0 -> 0x5C0300   the generic MAGIC aura   -- FFNx patches nothing
#     type 1 -> 0x5C0572   limit break              -- `limit-aura`
#     type 2 -> 0x5C06BF   enemy skill              -- this group
#     type 3 -> 0x5C0953   summon                   -- this group
#
# Type 0 has no constants to scale because FFNx paces it a completely different
# way: the effect60 slot function that drives the auras, 0x5C0AFF, is not one of
# the names FFNx excludes, so it gets an InterpolationEffectDecorator -- the
# pause-throttle. And the stock 0x5C0AFF reads g_is_battle_paused at +0x1A and
# returns early, so the pause trick genuinely stops it. That is `effect60-
# throttle`, and it is why the magic aura runs four times too fast without it.
#
# Types 2 and 3 are the reverse: FFNx puts them in effect60's NoEffectDecorator
# list -- full rate, constants scaled instead. Which means they need BOTH: the
# throttle must leave them alone (they are in EFFECT60_NO_THROTTLE) and their
# constants must move. Enabling `effect60-throttle` without these two groups
# leaves the enemy-skill and summon auras four times too fast.
#
# HOW THE FOLDED CONSTANTS WERE PLACED
#
# The resolver managed 4 of 9 for enemy skill and 1 of 2 for summon, because
# after translation there is no immediate equal to the x86 operand to search
# for. The recompiler folds `(K - x) << N` into a shifted-register subtract:
#
#   x86   mov eax,0xe ; sub eax,edx ; shl eax,7
#   arm64 mov w8,#0x700          ; sub w20, w8, w9, lsl #7
#
# FFNx rewrites that as `mov eax,56 ; sub ; shl 5`, and 56<<5 == 14<<7 == 0x700
# -- so THE CONSTANT DOES NOT MOVE AT ALL. Only the shift amount does, and the
# whole two-instruction FFNx patch collapses to one ARM64 word. Same for
# `<<10 -> <<8` (14<<10 == 56<<8 == 0x3800) and for the `imul eax,-4096` that
# became `neg w8, w8, lsl #12`.
#
# The summon aura is the same trick once more removed: x86 `shl eax,0xc` follows
# with `cdq ; and edx,7 ; add eax,edx ; sar eax,3`, and the recompiler folded the
# shift and the divide into a single `lsl w8, w8, #9`. FFNx's 12 -> 10 becomes
# 9 -> 7 there.
#
# The `ubfx w9, w8, #19, #3` two instructions earlier is the leftover `cdq;
# and edx,7`, which the fold made dead -- it writes guest EDX and the x86
# consumes EDX only in the `add` that was folded away. It is left alone: it
# yields 0 for every counter below 2^19, and this counter never exceeds 60.
NSO_GATED['aura-eskill'] = [
    ('enemy_skill_aura_effects_5C06BF+0x5C,+0x64  (14-c)<<7 -> (56-c)<<5, one '
     'word: the 0x700 constant is unchanged',
     0x007D86EC, 0x4B091D14, 0x4B091514),
    ('enemy_skill_aura_effects_5C06BF+0x81,+0x89  (14-c)<<10 -> (56-c)<<8, one '
     'word: the 0x3800 constant is unchanged',
     0x007D8764, 0x4B082935, 0x4B082135),
    ('enemy_skill_aura_effects_5C06BF+0xA7  phase boundary 8 -> 32',
     0x007D87D0, 0x7100213F, 0x7100813F),
    ('enemy_skill_aura_effects_5C06BF+0xD6,+0xD9  (c-8)<<12 -> (c-32)<<10, one '
     'word (the -0x8000 subtrahend is unchanged)',
     0x007D8808, 0x53144D08, 0x53165508),
    ('enemy_skill_aura_effects_5C06BF+0xB3  c * -4096 -> c * -1024',
     0x007D8834, 0x4B0833E8, 0x4B082BE8),
    ('enemy_skill_aura_effects_5C06BF+0x182 aura ends at frame 15 -> 60',
     0x007D8AA8, 0x71003D1F, 0x7100F11F),
]

NSO_GATED['aura-summon'] = [
    ('summon_aura_effects_5C0953+0x4D  (c<<12)/8 -> (c<<10)/8, folded to one '
     'lsl: 9 -> 7',
     0x007D9144, 0x53175908, 0x53196108),
    ('summon_aura_effects_5C0953+0x19D phase boundary 8 -> 32',
     0x007D9704, 0x7100211F, 0x7100811F),
]


# --------------------------------------------------------------------------
# `damage-numbers` -- how long a damage number stays on screen, and its bounce
#
# FFNx does three things to display_battle_damage_5BB410:
#
#   +0x54   frame count 11 -> 44                (a plain constant)
#   +0x1E2  y-offset table pointer -> a 44-entry table
#   +0x2D7  the same pointer, second read site
#
# The frame count alone resolved long ago, but it sits in `c-battle_damage`,
# excluded by hand because the table repoint "needs memory that exists only
# inside FFNx". That is why enabling effect100-scale did nothing for the damage
# numbers -- effect100 carries the ACTION TEXT ("Fire2", "Braver"), while the
# damage numbers are display_battle_damage_5BB410, which lives in effect60.
#
# Scaling the count without the table is actively bad: the counter indexes the
# table, so a 44-frame counter against a 12-entry table reads 32 bytes past it
# -- straight into effect10_array_data_8FE1F6, 14 bytes later. That is the
# "damage numbers in several places at once" bug from an earlier session.
#
# The table does not have to live inside FFNx. It has to live at a guest address
# the translated code can reach, and ff7_en has 324 bytes of linker alignment
# padding at the end of .rdata. 44 of those bytes hold FFNx's exact 60 FPS
# curve, and the ONE hoisted movz/movk pair that serves both read sites is
# repointed at it.
#
# The span was proved unused two ways before anything was written to it:
#   * no absolute dword anywhere in ff7_en points into it
#   * no movz/movk pair anywhere in the recompiled ARM64 builds an address
#     inside the whole 324-byte run
#
# Transcribed verbatim from FFNx animations.cpp y_pos_offset_display_damage_60.
DAMAGE_TABLE_VA = 0x7B9D80
DAMAGE_TABLE_60 = bytes([0, 1, 2, 3, 4, 5, 6, 6, 7, 7, 7, 8, 8, 8, 8, 8, 7, 7,
                         7, 6, 6, 5, 4, 3, 2, 1, 0, 0, 1, 2, 3, 4, 4, 4, 3, 2,
                         1, 0, 0, 1, 1, 0, 0, 0])
assert len(DAMAGE_TABLE_60) == 44

EXE_GATED['damage-numbers'] = [
    ('60fps damage y-offset table (44 bytes into .rdata padding)',
     DAMAGE_TABLE_VA, bytes(44), DAMAGE_TABLE_60),
]
NSO_GATED['damage-numbers'] = [
    ('display_battle_damage_5BB410+0x54 hold 11 -> 44 frames',
     0x007C5400, 0x52800168, 0x52800588),
    ('display_battle_damage y-offset table lo16 -> 0x%X' % DAMAGE_TABLE_VA,
     0x007C51B4, 0x529C3D18, 0x5293B018),
    ('display_battle_damage y-offset table hi16 -> 0x%X' % DAMAGE_TABLE_VA,
     0x007C51B8, 0x72A011F8, 0x72A00F78),
]


# --------------------------------------------------------------------------
# `tifa-slots` -- the spinning reel in Tifa's limit break
#
# THE GAP
# -------
# FFNx's Tifa handling is exactly three things, and we had two of them:
#
#   animations.cpp:284  effect100 field_1A *= mult, for both
#                       tifa_limit_1_2_sub_4E3D51 and tifa_limit_2_1_sub_4E48D4
#                       -> we have it, in EFFECT100_CASES
#   animations.cpp:1281 tifa_limit_2_1_sub_4E48D4+0x1FE, byte 12 -> 48
#                       -> we have it, in r-battle_aura
#   animations.cpp:1325 the "Tifa slots speed patch"
#                       -> WE DID NOT HAVE IT.  This group is it.
#
# The third one is why the reel spins in slow motion: the first two scale how
# long the limit *sequence* runs, and nothing scaled the reel's own animation.
# At 60 fps the reel therefore steps through its frames at a quarter of the
# rate the rest of the effect moves, which also drags the per-step sound along
# with it, and leaves the reel still turning when the attack starts.
#
# WHAT FFNx ACTUALLY CHANGES
# --------------------------
# Two bytes, and neither one is a frame count -- which is why the resolver,
# which searches for an immediate equal to a scaled x86 operand, could never
# have found them. In `display_tifa_slots_handler_6E3135`:
#
#   6E3293  0f bf 0c 45 00 3c dc 00  movsx ecx, word [eax*2 + 0xDC3C00]
#   6E329B  83 e1 07                 and ecx, 7           <- +0x168 is the 07
#   6E329E  8d 94 8a 44 01 00 00     lea edx,[edx+ecx*4+0x144]
#                  ^^                                     <- +0x16B is the SIB
#
#   +0x168:  07 -> 03    the reel phase wraps after 4 entries, not 8
#   +0x16B:  8A -> CA    the SIB scale field goes from x4 to x8
#
# Together they walk the same table at double stride over half the range: the
# reel visits entries 0,2,4,6 instead of 0..7, so one revolution takes half as
# many ticks and the reel turns twice as fast. FFNx writes these two literals
# unconditionally rather than deriving them from battle_frame_multiplier, so
# this group does the same. Doubling is what the reference implementation
# does at 60 fps; stepping x4 because our multiplier happens to be 4 would be
# my extrapolation rather than FFNx's behaviour, and this file does not ship
# extrapolations.
#
# LOCALISING IT IN THE ARM64
# --------------------------
# `x86_to_arm` maps function ENTRIES only, so neither byte can be mapped
# directly. These are anchored on their surrounding instructions, the way
# `field-text` had to be after the mis-resolution described above.
#
#   D49F70  ldrh w8, [x0]                        <- the movsx load
#   D49F74  ldr  w9, [x21, #8]                   <- edx
#   D49F78  and  w8, w8, #7      -> #3           <- PATCHED
#   D49F7C  str  w8, [x21, #4]
#   D49F80  add  w8, w9, w8, lsl #2  -> lsl #3   <- PATCHED  (the SIB scale)
#   D49F84  add  w19, w8, #0x144                 <- the +0x144 displacement
#
# The recompiler split the `lea` into a shifted add plus a displacement add,
# so the x86 SIB scale survives as the ARM64 shift amount and the change is
# the same single-field edit it is on x86.
#
# The assignment is unambiguous three times over: within the function body
# (D499B0..D4A450) there is exactly ONE `and w,w,#7` and exactly ONE
# `add ...,lsl #2` followed by `add ...,#0x144`, and the four-word window
# above occurs exactly ONCE in the whole module. See test_tifa_slots.py.
NSO_GATED['tifa-slots'] = [
    ('display_tifa_slots_handler_6E3135+0x168 reel phase wrap 8 -> 4 '
     '(and #7 -> #3)',            0x00D49F78, 0x12000908, 0x12000508),
    ('display_tifa_slots_handler_6E3135+0x16B reel table stride x4 -> x8 '
     '(lsl #2 -> #3)',            0x00D49F80, 0x0B080928, 0x0B080D28),
]


# --------------------------------------------------------------------------
# The battle MENU tick rate -- checked, and already correct. Do not re-derive.
#
# The reel is the only part of a limit break that lives in the battle menu, so
# "is Tifa's reel paced right" reduces to "how often does the menu tick", and
# that question is worth answering once in writing.
#
# `battle_loop_sub_41BAB3` does not call the menu once per battle tick. It
# calls it in a counted loop:
#
#   41BDE5  mov  dword [ebp-0x18], 4        ; the sub-step count
#   41BE1B  call 0x6CE8B3                   ; battle_menu_update, EVERY pass
#   41BE2B  call 0x6CEE84                   ; the DRAW, only on i == count-1
#
# `[ebp-0x18]` is written once, with 4, and read only by that loop. Vanilla
# therefore ticks the battle at 15 Hz and the MENU at 15 x 4 = 60 Hz, which is
# why FF7's battle menu has always been smoother than its models.
#
# Left alone at 60 fps that would put the menu at 240 Hz. It is NOT left
# alone: `fps-menu` above rewrites the same word (+0x90AE8) to 1, so the menu
# ticks once per 60 Hz battle tick -- 60 Hz, exactly vanilla. Its comment
# calls it "the least corroborated patch in the file" because the resolver
# could not reproduce FFNx's `patch_divide_code<byte>(battle_fps_menu_
# multiplier, 4)`. It is corroborated now, from the other end: reading the
# loop shape lands on the same offset and the same value, and the
# `mov wzr / mov #4 / mov #1 / str` sequence occurs exactly once in the
# function.
#
# Consequences worth keeping:
#
#   * the reel counter (incremented once per menu update in 0x6E2170) already
#     runs at the vanilla rate, so `tifa-slots` makes the reel 2x VANILLA --
#     which is FFNx's deliberate choice at 60 fps, not a correction of ours.
#     `--disable tifa-slots` gives literal vanilla reel speed.
#   * the reel's LANDING condition -- `counter & (period-1) == 0`, period from
#     the table at 0x91EAC8 (02 04 08 10, by limit level) -- is on that same
#     already-correct clock, so the reel is not what releases Tifa early.
#   * of every function this project patches, exactly TWO are reachable from
#     `battle_menu_update_6CE8B3`: `battle_menu_closing_window_box_6DAEF0`
#     and `display_tifa_slots_handler_6E3135`. Nothing else in the patch set
#     is on the menu clock at all.


AUTORUN_GROUP = 'no-autorun'
NOCHEATS_GROUP = 'no-cheats'


def _register_input_tweaks():
    """
    Two independent input tweaks; see ff7nx_nocheats.py for the derivations.

    `no-autorun` is one word and goes through the ordinary gated word-patch
    path, because that is all it is. `no-cheats` needs an extra instruction
    before an existing store, so it is a pair of two-word caves and goes in
    reclaimed padding like `analog-360` -- it costs the 60 FPS cave budget
    nothing.
    """
    import ff7nx_nocheats as NC
    NSO_GATED[AUTORUN_GROUP] = NC.autorun_patch()


ANALOG_GROUP = 'analog-360'
ANALOG_DIAG_ENV = 'SEVENTH_NX_ANALOG_DIAG'


def analog_diag():
    """
    Diagnostic build: ignore the stick and rotate by a fixed 45 degrees
    whenever a direction is held.

    The failure mode of this feature is silence -- every branch that cannot
    proceed leaves the control direction alone, which looks exactly like the
    group being off. That is right for shipping and useless for debugging, so
    this makes it say something. With it on:

        the character walks 45 degrees off  ->  the hook, the input object,
            the key mask, the level-data walk and the byte write are ALL
            working, and the only thing left is the stick read;
        nothing changes at all              ->  the cave is not running, or
            it is bailing before the write.

    One build, and it splits the problem in half.
    """
    return os.environ.get(ANALOG_DIAG_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def analog_360_caves(enabled, log=print):
    """
    360 degree field movement -- see ff7nx_analog.py for the mechanism.

    ONE cave, at `0x947CF0`, inside the recompiled `field_loop_sub_63C17F`
    immediately after its single call to the field's input read. It resolves
    the port's input object the way the port's own axis getters do, reads the
    stick and the four direction scancodes, and writes the field's control
    direction byte.

    There used to be a second cave on the port's input poll (0x111BFC0) whose
    only job was to record the object's `this` pointer. It is gone. That was
    the one assumption in this feature that had never been measured -- the
    object being polled need not be the object the game reads -- and it is
    exactly the assumption that made the first hardware test do nothing. The
    resolution at 0x1DC0 that this cave copies is the port's own, is shared by
    every one of its axis and button getters, and is therefore provably the
    object whose floats become the scancodes this cave reads.

    WHERE IT LIVES. Marked `place: padding`, so it is chained through
    reclaimed inter-function alignment padding (ff7nx_cave.py) instead of the
    2,464-byte gap between .text and .rodata, which the shipping preset fills.
    The two lookup tables go to .rodata's tail. Nothing has to be given up.

    OFF BY DEFAULT and excluded from --enable-all, like every other group that
    injects code.
    """
    if not enabled:
        return []
    try:
        import ff7nx_analog as AN
        import ff7nx_analog_cave as AC
    except ImportError as exc:
        raise SystemExit('ABORT  %s requested but %s' % (ANALOG_GROUP, exc))
    diag = analog_diag()
    if diag:
        log('  !!  %s=1: the stick is IGNORED and every held direction is '
            'rotated 45 degrees.' % ANALOG_DIAG_ENV)
        log('      This is a diagnostic build. If the character walks at 45 '
            'degrees to where it should,')
        log('      everything except the stick read is working. If nothing '
            'changes, the cave is not running.')
    # Everything this group reads, pinned. See ff7nx_analog.py for how each
    # was derived; the point of repeating them here is that the cave's
    # correctness rests on these words and on nothing else, so a module that
    # does not have them is refused instead of quietly writing rubbish.
    obj_resolution = [
        (0x1DC0, 0xB0009668, 'the port resolves its input object at 0x1DC0 '
                             'with adrp x8, #0x12ce000'),
        (0x1DC4, 0xF940E908, 'ldr x8, [x8, #0x1d0]'),
        (0x1DC8, 0xF9400108, 'ldr x8, [x8]'),
        (0x1DCC, 0xF9400508, 'ldr x8, [x8, #8]'),
        (0x1DD0, 0xF9400108, 'ldr x8, [x8]'),
        (0x1DD4, 0xF9404500, 'ldr x0, [x8, #0x88] -- the object itself; this '
                             'cave copies those six words exactly'),
        (0x111BF6C, 0xBC5F0100,
         'GetAxis: `ldur s0, [x8, #-0x10]` is what makes axis id 0x10 mean '
         'object +0x30, and 0x10..0x13 the four left-stick half-axes'),
        (0x111BE14, 0xBD47B901,
         'IsHeld thresholds those same floats at the port deadzone'),
        (0x111C168, 0xBD003265,
         'the poll stores the stick UP half at object +0x30'),
        (0x111C1BC, 0xBD003E61,
         'and the LEFT half at +0x3C -- the four floats the cave differences'),
    ]
    keybuf = [
        (0x10D3948, 0x910A635A,
         'the DirectInput emulation bases its key writes at 0x12CF298'),
        (0x10D3A5C, 0x39005119,
         'and stores at +0x14 of it, i.e. KEYBUF 0x12CF2AC'),
        (0x10D3938, 0x321903F9,
         'writing 0x80, which is why the cave shifts right by 7'),
        (0x10D3984, 0x52800908, 'stick-up  -> DIK_UP 0x48'),
        (0x10D39B4, 0x52800A08, 'stick-down -> DIK_DOWN 0x50'),
        (0x10D39BC, 0x528009A8, 'stick-right -> DIK_RIGHT 0x4D'),
        (0x10D39C4, 0x52800968, 'stick-left -> DIK_LEFT 0x4B'),
    ]
    consumer = [
        (0x9D8110, 0x11002500,
         'the consumer computes triggers+9 in GUEST space '
         '(field_update_models_positions, x86 0x634B4D)'),
        (0x9D8118, 0x941C90A2,
         'translates it'),
        (0x9D811C, 0x39C00008,
         'and reads ONE SIGNED BYTE -- the only read of the control '
         'direction anywhere in the executable, and what this cave writes'),
    ]
    translator = [
        (0x10FC3AC, 0x530C7C09,
         'the translator is a 4 KB page table (`lsr w9, w0, #0xc`), which is '
         'why the cave translates whole guest addresses and never indexes '
         'off a pointer it got back'),
        (0x10FC3C0, 0x8B090109,
         'and adds only the low 12 bits of the address to the page base'),
    ]
    return [
        dict(tag=ANALOG_GROUP, kind='analog_field',
             site={'hook': AN.FIELD_HOOK,
                   'sig': [(-4, 0x9402BE15),     # bl   the field input read
                           (0, 0x29422728),      # ldp  w8, w9, [x25, #0x10]
                           (4, 0xB9400336),      # ldr  w22, [x25]
                           (12, 0x51006120)],    # sub  w0, w9, #0x18
                   'assert': obj_resolution + keybuf + consumer + translator,
                   'place': 'padding'},
             label='360 movement: field control direction  [rev2 '
                   'chain-resolved object]'
                   + ('  [DIAGNOSTIC: fixed 45 degrees]' if diag else ''),
             build=AC.build_field_cave,
             diag=diag),
    ]


def nocheats_caves(enabled, log=print):
    """
    Make the right stick click do nothing.

    Two caves, one on each of the poll's two button stores, each clearing the
    StickR bit out of the mask on its way into the object. That object is the
    only place in the module the physical buttons exist -- `main` reads
    nn::hid in exactly one function -- so this removes R3 from everything that
    could act on it, including the boosters.

    It cannot disturb the game's own input: the DirectInput key emulation maps
    ids 2..0x13 and both stick clicks are ids 0 and 1, outside that range.
    """
    if not enabled:
        return []
    import ff7nx_nocheats as NC
    return [
        dict(tag=NOCHEATS_GROUP, kind='nocheats', place_body=NC.nocheats_body(),
             site={'hook': va,
                   'sig': [(0, NC.BUTTON_STORE_ORIG)],
                   'assert': [
                       (0x111C028, 0x9400D5EE,
                        'the poll is the module\'s only nn::hid read '
                        '(GetNpadState, FullKey)'),
                       (0x111C0C0, 0x9400D5CC,
                        'and the only Handheld one -- so obj+0x20 is the only '
                        'place the physical buttons exist'),
                       (0x111BFD0, 0xF9401008,
                        'the poll copies the previous button mask from '
                        'obj+0x20'),
                       (0x111BFDC, 0xF9001408, 'to obj+0x28'),
                       # (the id->bit table at 0x11DDAE4 maps id 1 to bit 5,
                       #  StickR -- .rodata, so not checkable here; see
                       #  ff7nx_nocheats.py)
                       (0x10D3968, 0x51000908,
                        'the DirectInput key loop subtracts 2 from the id'),
                       (0x10D396C, 0x7100451F,
                        'and takes only 0..0x11 -- so ids 0 and 1, the two '
                        'stick clicks, are mapped to no scancode at all and '
                        'masking one takes nothing away from the game'),
                   ],
                   'place': 'padding'},
             label='no cheats: right stick click ignored (store %d of 2)' % (k + 1),
             build=None)
        for k, va in enumerate(NC.BUTTON_STORES)
    ]


def _place_padding_caves(padded, text, raw, segs, nso_path, log=print):
    """
    Put a group's caves in reclaimed inter-function alignment padding.

    The 2,464-byte gap between .text and .rodata cannot be enlarged --
    .rodata's address is baked into every adrp that reaches it -- and the
    shipping 60 FPS preset leaves 16 bytes of it. But the recompiled functions
    are 16-byte aligned, so nearly every one ends in one to three words of zero
    padding: 60 KB that passes every test in cave_space.py. A cave chained
    through those costs the tail gap nothing, so no existing group has to be
    dropped, weakened or re-verified to make room for a new one.

    Three things make this safe rather than merely clever:

      * every hole is re-checked as STILL ZERO in `text` as it stands right
        now, not trusted from a scan of the stock file -- so a hole another
        pass already claimed is skipped;
      * the whole cave is confined to one 512 KB window, which keeps its own
        `b.cond` and `cbz` inside their +/-1 MB reach, and a64's encoders raise
        rather than wrap if that ever stopped being true;
      * the words written are recorded on the descriptor, so the NSO
        self-check and the .text diff audit can both account for every one of
        them instead of reporting them as unexplained changes.

    Structural facts about the module -- which addresses are function starts,
    and which are named by a data word -- come from the STOCK file, because
    patching does not change them and re-deriving them from a half-patched
    image would be wrong. Zero-ness comes from `text`, because that is exactly
    what patching does change.
    """
    import cave_space
    import ff7nx_cave
    import nxmap
    import ff7nx_analog as _AN
    import ff7nx_analog_cave as _AC

    if not nso_path:
        raise SystemExit(
            'ABORT  %s needs the padding-hole map, which is derived from the '
            'stock module,\n       and patch_nso was not told where that is. '
            'Pass nso_path=.' % padded[0]['tag'])

    # --- the lookup tables go in .rodata's own tail, already mapped R--,
    # the same claim the shared-prologue descriptor table makes. .data's VA is
    # a fixed header field independent of .rodata's declared size, so this
    # cannot move or shrink anything else.
    rodata_va, data_va = segs[1][1], segs[2][1]
    tbl_va = rodata_va + len(raw[1])
    tables = bytes(_AN.SNAP_TAB) + bytes(_AN.ATAN_TAB)
    if data_va - tbl_va < len(tables):
        raise SystemExit(
            'ABORT  360 movement needs %d bytes in the .rodata tail gap '
            '(0x%X..0x%X), only %d available.'
            % (len(tables), tbl_va, data_va, data_va - tbl_va))
    raw[1] = raw[1] + tables
    snap_va = tbl_va
    atan_va = tbl_va + len(_AN.SNAP_TAB)

    m = nxmap.Main(nso_path)
    starts = set(m.arm_starts)
    named = cave_space.named_targets(m.img[cave_space.RODATA:])
    pool = ff7nx_cave.HolePool(text, starts=starts, named=named)
    log('      padding pool: %d verified hole(s), %d usable word(s)'
        % (len(pool.free), sum(max(0, n - 1) for _, n in pool.free)))
    log('      360 movement tables -> .rodata +0x%X (%d bytes)'
        % (tbl_va - rodata_va, len(tables)))

    for c in padded:
        hook = c['site']['hook']
        try:
            if c.get('place_body') is not None:
                # A straight-line cave: body, then the instruction it
                # displaced, then back. No internal branches, so it needs
                # none of emit_laid_out's layout machinery.
                orig = struct.unpack_from('<I', text, hook)[0]
                placed, entry = ff7nx_cave.emit_hooked(
                    pool, hook, orig, c['place_body'])
                placed.pop(hook)          # the hook branch is written below
            else:
                if c['build'] is _AC.build_field_cave:
                    build = (lambda cave, addr=None, s=snap_va, t=atan_va,
                             d=c.get('diag', False):
                             _AC.build_field_cave(cave, s, t, addr, d))
                else:
                    build = c['build']
                entry, placed = ff7nx_cave.emit_laid_out(pool, build)
        except ff7nx_cave.NoRoom as exc:
            raise SystemExit('ABORT  %s: %s' % (c['label'], exc))
        for va, w in placed.items():
            if struct.unpack_from('<I', text, va)[0] != 0:
                raise SystemExit(
                    'ABORT  %s wanted padding at +0x%X and it is not zero -- '
                    'refusing to overwrite it' % (c['label'], va))
            struct.pack_into('<I', text, va, w)
        struct.pack_into('<I', text, hook,
                         0x14000000 | (((entry - hook) >> 2) & 0x3FFFFFF))
        c['placed'] = placed
        c['tables'] = (snap_va, atan_va)
        span = max(placed) - min(placed)
        log('  ok  +0x%06X  -> padding, entry 0x%06X (%3d words across %2d '
            'hole(s), 0x%X span)  %s'
            % (hook, entry, len(placed), len(pool.used), span, c['label']))
        pool.used = []



LIMITER_DIVISORS = (
    # (label, address, the value the 60 FPS set writes there)
    ('field limiter divisor',  0x7B7840, 60.0),
    ('battle limiter divisor', 0x7C0B00, 60.0),
)


def frame_pacing_note():
    """
    Why the limiters produce slightly under 60 frames a second, and why the
    fix is a SMALL nudge rather than a large one.

    Per frame, in the field (0x6388A1 and 0x6388CC are 0x2B bytes apart in the
    same loop body):

        0x6388A1  call 0x60E96C     <- baseline 0xCFF8D8 = now.  FRAME START
                  ... work ...
        0x6388CC  call 0x638655     <- the limiter: spin until
                                       (now - baseline) >= frame_time
                  ... TAIL: everything after the limiter releases, up to the
                      next frame's baseline reset ...

    The limiter only governs baseline -> release. **The tail is added on top
    and nothing compensates for it**, so the real period is

        period = frame_time + tail

    and the production rate is 1/(frame_time + tail), which is below 60 even
    though frame_time alone is below 1/60. The debt accumulator does not help:
    it is measured from the same baseline, reads ~0 on a clean frame, and is
    zeroed on every normal spin exit (0x6387BE).

        field   frame_time = cps/0x7B7840 - 10000.0   -> 16.146 ms at 60
        battle  frame_time = cps/0x7C0B00             -> 16.667 ms at 60

    Battle has no early-exit margin at all, so at divisor 60 its period is
    1/60 PLUS its tail -- it can never reach 60.

    HOW THE DISPLAY TURNS THAT INTO 57-59. Presents are capped at 60 (measured:
    at divisor 240 the counter reads exactly 60 and never more). When the game
    produces slightly under 60 frames a second, some refreshes get no new frame
    and the swap count drops to 57-59. When it produces MORE than 60, every
    refresh is fed and the counter reads a solid 60 -- while the game logic,
    which is not capped by the display, runs at the production rate. That is
    why divisor 240 reads 60 and plays roughly three times too fast.

    SO THE CORRECT VALUE IS THE SMALLEST ONE THAT HOLDS 60. At that divisor
    production is exactly 60/s: no refresh is starved and the pace is right.
    Anything higher buys nothing and runs the game fast, invisibly, because
    the frame counter stays pinned at 60 either way. Expect it to land around
    61-66 depending on how long the tail is; 70 is already several percent
    fast.

    This is why the setting is a dial with fine steps and not a switch, and
    why the label says to use the lowest value that works.
    """


def retarget_limiters(exe_patches, fps):
    """
    Rewrite the limiter divisors in an already-built patch list.

    Rewrites rather than appends: the 60 FPS set already patches both of these
    addresses, and two patches on one address would fail the "expected old
    bytes" check on the second. Aborts if either is missing, so a future
    reshuffle of EXE_CONFIRMED cannot leave this silently doing half its job.
    """
    if not 30.0 <= fps <= 1000.0:
        raise SystemExit('ABORT  --limiter-fps %g is outside 30..1000' % fps)
    out, hit = [], set()
    for label, va, old, new in exe_patches:
        for what, addr, base in LIMITER_DIVISORS:
            if va == addr and new == struct.pack('<d', base):
                out.append(('%s %g -> %g (frame pacing)'
                            % (what, base, fps), va, old,
                            struct.pack('<d', fps)))
                hit.add(addr)
                break
        else:
            out.append((label, va, old, new))
    missing = [w for w, addr, _ in LIMITER_DIVISORS if addr not in hit]
    if missing:
        raise SystemExit(
            'ABORT  --limiter-fps needs the 60 FPS divisor patches to be '
            'present and unmodified;\n       could not find: %s'
            % ', '.join(missing))
    return out


_register_input_tweaks()


def dispatch_caves(scale_tags, throttle_tags, batt_mult, extra_exclude=(),
                   allow_tags=(), throttle_only=(), log=print):
    """
    Build the cave descriptors for every enabled dispatcher group.

    Per dispatcher, at most four caves:

        add_fn_to_*    ONE cave, whether it is seeding the first-frame flag,
                       the throttle byte, or both. Two caves cannot hook the
                       same instruction, so this has to be merged rather than
                       emitted twice -- and a build that silently dropped one of
                       them would leave a flag nothing consumes.
        execute_*      the first-frame arithmetic scaler          (*-scale)
        execute_*      the pre-call half of the pause-throttle    (*-throttle)
        execute_*      the post-call half of the pause-throttle   (*-throttle)

    `cave_at` is filled in by the caller, which knows where .text ends.
    """
    # Cave placement order decides cave ADDRESSES, so it decides the output
    # md5. The scale tags keep the order they arrive in and throttle-only tags
    # are appended after, which is what makes a build with no throttle group
    # byte-identical to the one before this change existed -- the only way an
    # A/B test of the throttle means anything.
    tags = list(scale_tags)
    tags += [t for t in throttle_tags if t not in tags]
    if not tags:
        return []
    if _disp is None:
        raise SystemExit('ABORT  dispatcher groups requested but '
                         'ff7nx_dispatch_sites.py is missing -- regenerate it:\n'
                         '       python3 ff7nx_locate.py --exe ff7_en '
                         '--nso exefs/main --emit ff7nx_dispatch_sites.py')
    shift = {2: 1, 4: 2, 8: 3}.get(batt_mult)
    if shift is None:
        raise SystemExit('ABORT  battle multiplier must be 2, 4 or 8')
    freq_bits = shift                 # `and #(2**shift - 1)`: 1-in-batt_mult
    out = []
    for tag in tags:
        d = DISPATCH_SITES[tag]
        want_scale = tag in scale_tags
        want_thr = tag in throttle_tags
        thr = None
        if want_thr:
            thr = d.get('throttle')
            if thr is None:
                raise SystemExit('ABORT  %s has no throttle site in '
                                 'ff7nx_dispatch_sites.py -- regenerate it'
                                 % tag)
            thr = dict(thr)
            if tag in allow_tags:
                # Allow-list: throttle ONLY these, everything else untouched.
                thr['allow'] = True
                thr['table'] = [va for _n, va in thr.get('aura_allow', ())]
                for extra in throttle_only:
                    if extra not in thr['table']:
                        thr['table'].append(extra)
                        log('       throttle: %s also -- 0x%06X (--throttle-only)'
                            % (tag, extra))
                if not thr['table']:
                    raise SystemExit('ABORT  %s allow-list is empty -- '
                                     'regenerate ff7nx_dispatch_sites.py' % tag)
                for n, va in thr.get('aura_allow', ()):
                    log('       throttle: %s only -- %s (0x%06X)'
                        % (tag, n, va))
            else:
                thr['table'] = throttle_table(tag, thr, extra_exclude, log)

        flag = d['flag'] if want_scale else None
        out.append(dict(
            tag=tag, kind='add_fn', site=d['add_hook'],
            label='%s registration (%s)'
                  % (tag, ' + '.join(filter(None, [
                      'first-frame flag' if want_scale else None,
                      ('throttle seed, %d ONLY' % len(thr['table'])
                       if thr.get('allow') else
                       'throttle seed, %d excluded' % len(thr['table']))
                      if thr else None]))),
            build=lambda cave, d=d, flag=flag, thr=thr:
                _disp.build_addfn_cave(cave, d['add_hook'], flag,
                                       d['mask_bits'], thr)))

        if want_scale:
            sym = {n: va for n, va, _o, _g in d['cases']}
            cases = [(n, o, g) for n, _v, o, g in d['cases']]
            if not cases:
                raise SystemExit('ABORT  %s has no arithmetic cases, so there '
                                 'is no first-frame scaler to build' % tag)
            out.append(dict(
                tag=tag, kind='dispatcher', site=d['disp_hook'],
                label='%s first-frame scaler (%d case(s), x%d)'
                      % (tag, len(cases), batt_mult),
                build=lambda cave, d=d, cases=cases, sym=sym: _disp.build_cave(
                    cave, d['disp_hook'], d['flag'], d['mask_bits'],
                    d['data_base'], d['stride'], d['fields'], cases, sym,
                    shift)))

        if want_thr:
            pre = dict(hook=thr['pre'], displaced=thr['pre_displaced'],
                       sig=thr['pre_sig'], ctx_reg=thr['ctx_reg'],
                       idx_off=d['disp_hook']['idx_off'],
                       store_reg=thr['store_reg'])
            post = dict(hook=thr['post'], displaced=thr['post_displaced'],
                        sig=thr['post_sig'])
            out.append(dict(
                tag=tag, kind='throttle_pre', site=pre, throttle=thr,
                label='%s pause-throttle pre-call (1 step in %d)'
                      % (tag, batt_mult),
                build=lambda cave, pre=pre, thr=thr, d=d:
                    _disp.build_throttle_pre_cave(
                        cave, pre, thr, PAUSED_GUEST, thr['mask_bits'],
                        freq_bits)))
            out.append(dict(
                tag=tag, kind='throttle_post', site=post, throttle=thr,
                label='%s pause-throttle post-call' % tag,
                build=lambda cave, post=post, thr=thr:
                    _disp.build_throttle_post_cave(cave, post, thr)))
    return out


def camera_wait_caves(enabled, batt_mult, log=print):
    """
    One cave per camera script interpreter, scaling opcode 0xF5's wait operand.

    Returned in the same shape as the dispatcher caves so patch_nso can verify
    and place them through the same path -- including the stock-signature check,
    which matters more here than anywhere else: the hook is a `strh` inside a
    1,500-instruction interpreter and the two neighbouring stores to the same
    field belong to opcode 0xF4 and to an initialiser.
    """
    if not enabled:
        return []
    if _disp is None or not CAMERA_WAIT_SITES:
        raise SystemExit('ABORT  camera-wait requested but '
                         'ff7nx_dispatch_sites.py has no CAMERA_WAIT_SITES -- '
                         'regenerate it:\n'
                         '       python3 ff7nx_locate.py --exe ff7_en '
                         '--nso exefs/main --emit ff7nx_dispatch_sites.py')
    shift = {2: 1, 4: 2, 8: 3}.get(batt_mult)
    if shift is None:
        raise SystemExit('ABORT  battle multiplier must be 2, 4 or 8')
    out = []
    for tag in sorted(CAMERA_WAIT_SITES):
        s = CAMERA_WAIT_SITES[tag]
        out.append(dict(
            tag=tag, kind='camera_wait', site=s,
            label='%s opcode F5 wait x%d' % (tag, batt_mult),
            build=lambda cave, s=s: _disp.build_camera_wait_cave(cave, s,
                                                                 shift)))
    return out


def field_wait_caves(enabled, mult, log=print):
    """
    One cave scaling the FIELD script's opcode 0x24 (WAIT) frame count.

    Same shape and the same verification path as camera_wait_caves -- the
    stock-signature check is doing real work here too, because three other
    `strh` in this handler write the same wait_frames slot.

    `mult` is the COMMON frame multiplier (field is natively 30 FPS, so 2 at
    60), not the battle one. Getting that wrong by using --batt-mult would
    make every scripted pause four times as long.
    """
    if not enabled:
        return []
    if _disp is None or not FIELD_WAIT_SITES:
        raise SystemExit('ABORT  field-wait requested but '
                         'ff7nx_dispatch_sites.py has no FIELD_WAIT_SITES -- '
                         'regenerate it:\n'
                         '       python3 ff7nx_locate.py --exe ff7_en '
                         '--nso exefs/main --emit ff7nx_dispatch_sites.py')
    shift = {2: 1, 4: 2, 8: 3}.get(mult)
    if shift is None:
        raise SystemExit('ABORT  --scaler-mult must be 2, 4 or 8 for field-wait')
    # DIAGNOSTIC OVERRIDE. Not a feature -- a probe.
    #
    # `md1stin`'s guards arrive over the opening FMV far too early, and four
    # corrections have failed to move them. The open question is whether the
    # field script's WAIT chain in `dir` s0 times that appearance at all:
    #
    #     0428  WAIT 30 / REQ av_m      042E  WAIT 33 / REQ gu1
    #     0434  WAIT 30 / REQ gu0       043A  WAIT 60 / REQ av_l  ...
    #
    # Multiplying every WAIT by 8 instead of 2 makes a WAIT-timed event
    # arrive four times later -- seconds, not frames, and impossible to
    # misread. If the guards do not budge, nothing in `wait_frames[]` is what
    # schedules them and the whole family of field-clock theories is dead.
    #
    # Everything else scripted slows down with it, so a build made this way
    # is for one viewing of the opening, not for playing.
    env = os.environ.get('SEVENTH_NX_FIELD_WAIT_MULT')
    if env:
        probe = {'2': 1, '4': 2, '8': 3}.get(env.strip())
        if probe is None:
            raise SystemExit('ABORT  SEVENTH_NX_FIELD_WAIT_MULT must be '
                             '2, 4 or 8 (got %r)' % env)
        if probe != shift:
            log('DIAGNOSTIC: field WAIT x%s instead of x%d '
                '(SEVENTH_NX_FIELD_WAIT_MULT). Every scripted pause in the '
                'game is affected -- this build is a probe, not a playable '
                'one.' % (env.strip(), mult))
        shift, mult = probe, int(env.strip())
    out = []
    for tag in sorted(FIELD_WAIT_SITES):
        s = FIELD_WAIT_SITES[tag]
        out.append(dict(
            tag=tag, kind='field_wait', site=s,
            label='field script WAIT x%d' % mult,
            build=lambda cave, s=s: _disp.build_field_wait_cave(cave, s,
                                                                shift)))
    return out


# Injects code AND requires a matching change to the movie set on disk (every
# movie at the doubled rate), so it is never part of --enable-all: build.py
# turns it on explicitly, together with that change.
MOVIE_FPS_GROUP = 'movie-fps'


def movie_frame_caves(enabled, ratio=2, log=print):
    """
    The cave that divides the movie frame counter -- see
    ff7nx_dispatch.build_movie_frame_cave for the mechanism.

    ONLY CORRECT IF EVERY MOVIE IS AT THE DOUBLED RATE. The divider is
    unconditional: it does not know which movie is playing, so a 15 fps movie
    left in data/movies would have its counter halved and break in the
    opposite direction. build.py emplaces the whole movie set at 30 fps when
    this group is on, and the GUI drives both from one checkbox.
    """
    if not enabled:
        return []
    if _disp is None:
        raise SystemExit('ABORT  %s requested but ff7nx_dispatch.py is not '
                         'importable' % MOVIE_FPS_GROUP)
    shift = {2: 1, 4: 2, 8: 3}.get(ratio)
    if shift is None:
        raise SystemExit('ABORT  movie fps ratio %r must be 2, 4 or 8'
                         % ratio)
    stub = _disp.MOVIE_FRAME_STUB
    site = {
        'hook': _disp.MOVIE_FRAME_TAILCALL,
        # The whole three-word stub, so a build where get_movie_frame is not
        # this native trampoline is rejected instead of silently mangled.
        'sig': [(-8, 0x528000A0),          # mov  w0, #5
                (-4, 0x72B60160),          # movk w0, #0xb00b, lsl #16
                (0, 0x17FF209E)],          # b    #0xa510
    }
    del stub
    return [dict(
        tag='movie-frame', kind='movie_frame', site=site,
        label='movie frame counter / %d (30 fps FMV support)' % ratio,
        build=lambda cave, sh=shift: _disp.build_movie_frame_cave(cave, sh))]


MOVIE_POLL_GROUP = 'movie-poll'


def movie_poll_caves(enabled, mult=2, log=print):
    """
    Halve what MVIEF reports -- see ff7nx_dispatch.build_movie_poll_cave.

    MVIEF does not return the movie's frame. It returns a count of how many
    times the field script has polled since the movie started, incremented one
    per field tick. At 60 FPS that count runs twice as fast as the scripts
    expect, so models composited over a cutscene appear halfway through it.

    This is a 60 FPS artifact, NOT an FMV-mod one: the game's own 15 fps
    movies have it too. It is a separate group rather than part of the
    shipping preset so that a build without it reproduces byte for byte.
    """
    if not enabled:
        return []
    if _disp is None:
        raise SystemExit('ABORT  %s requested but ff7nx_dispatch.py is not '
                         'importable' % MOVIE_POLL_GROUP)
    shift = {2: 1, 4: 2, 8: 3}.get(mult)
    if shift is None:
        raise SystemExit('ABORT  movie poll multiplier %r must be 2, 4 or 8'
                         % mult)
    site = {'hook': _disp.MVIEF_POLL_HOOK, 'sig': _disp.MVIEF_POLL_SIG}
    return [dict(
        tag='movie-poll', kind='movie_poll', site=site,
        label='MVIEF poll counter / %d (movie overlay sync)' % mult,
        build=lambda cave, sh=shift: _disp.build_movie_poll_cave(cave, sh))]


# --------------------------------------------------------------------------
# `movie-update` -- the field's clock during an FMV
#
# The cause the other two movie groups only treat symptoms of. Switch's
# `update_movie_sample` is a native stub whose implementation BLOCKS until one
# new decoded frame is ready, and the field calls it once per loop iteration.
# The field therefore ticks once per MOVIE frame, not once per 30th or 60th of
# a second: 15 times a second on the port's own movies, 30 on an emplaced
# 30 fps set. Every per-tick clock in the field doubles with it -- `WAIT`
# countdowns, MVIEF polls, `MOVE` steps, animation -- which is why models
# composited over the opening (`md1stin`'s guards: a WAIT chain in `dir` s0,
# then a MOVE and a `VISI 0` in `gu0`/`gu1` s3) slide in halfway through the
# FMV and vanish.
#
# This group consumes the extra decoded frames inside the stub, so the guest
# sees one update per vanilla-rate frame and the whole field clock is vanilla
# again. See ff7nx_dispatch.build_movie_update_cave for the mechanism and for
# the smoothness it trades away.
MOVIE_UPDATE_GROUP = 'movie-update'


def movie_update_caves(enabled, ratio=2, log=print):
    """
    The cave that consumes `ratio - 1` extra decoded frames per guest call.

    ONLY CORRECT IF EVERY MOVIE IS AT THE MULTIPLIED RATE, for the same
    reason as movie-fps: the cave is unconditional and cannot tell which movie
    is playing. build.py emplaces the whole movie set at 30 fps when this
    group is on, and the GUI drives both from one checkbox.
    """
    if not enabled:
        return []
    if _disp is None:
        raise SystemExit('ABORT  %s requested but ff7nx_dispatch.py is not '
                         'importable' % MOVIE_UPDATE_GROUP)
    if ratio not in (2, 4, 8):
        raise SystemExit('ABORT  movie update ratio %r must be 2, 4 or 8'
                         % ratio)
    site = {
        'hook': _disp.MOVIE_UPDATE_TAILCALL,
        # The whole three-word stub, so a build where update_movie_sample is
        # not this native trampoline is rejected instead of silently mangled.
        'sig': [(-8, 0x52800080),          # mov  w0, #4
                (-4, 0x72B60160),          # movk w0, #0xb00b, lsl #16
                (0, 0x17FF37AA)],          # b    #0xa510
    }
    return [dict(
        tag='movie-update', kind='movie_update', site=site,
        label='movie update x%d (field clock during an FMV)' % ratio,
        build=lambda cave, n=ratio - 1:
            _disp.build_movie_update_cave(cave, n))]


def field_blink_caves(enabled, log=print):
    """
    The two caves that hold the eyes shut for a second frame.

    Both or neither. The test cave alone would take the shut arm twice and
    reload twice, halving the interval; the hold cave alone would write a -1
    the unwidened `cbz` reads as "eyes open", so the counter would run
    negative and never blink again. They are emitted together and share a
    group for that reason -- there is no useful bisect between them.
    """
    if not enabled:
        return []
    if _disp is None or not FIELD_BLINK_SITES:
        raise SystemExit('ABORT  %s requested but ff7nx_dispatch_sites.py has '
                         'no FIELD_BLINK_SITES' % FIELD_BLINK_GROUP)
    build = {'test': _disp.build_field_blink_test_cave,
             'hold': _disp.build_field_blink_hold_cave}
    missing = set(build) - set(FIELD_BLINK_SITES)
    if missing:
        raise SystemExit('ABORT  %s needs both sites, missing: %s'
                         % (FIELD_BLINK_GROUP, ', '.join(sorted(missing))))
    out = []
    for tag in sorted(FIELD_BLINK_SITES):
        s = FIELD_BLINK_SITES[tag]
        out.append(dict(
            tag='blink-' + tag, kind='field_blink', site=s,
            label='eye blink %s -- %s' % (tag, s['what']),
            build=lambda cave, s=s, f=build[tag]: f(cave, s)))
    return out


def throttle_table(tag, thr, extra_exclude, log=print):
    """
    The exclusion table the registration cave walks, as a list of guest
    addresses.

    `extra_exclude` is the bisect lever. The exclusion policy here is
    allow-by-default -- FFNx throttles everything it does not name -- so the
    failure mode when it is wrong is "one specific effect misbehaves", and the
    way to find it is to move suspects across one at a time rather than to turn
    the whole group off. A name that is not in the group's list is refused
    rather than ignored, because a typo would otherwise look like a clean
    negative result.
    """
    out = [va for _n, va in thr['exclude']]
    known = list(thr['exclude']) + list(thr.get('candidates', ()))
    for want in extra_exclude:
        key = want.upper().replace('0X', '')
        hit = [(name, va) for name, va in known
               if name == want or name.endswith('_%s' % want)
               or ('%06X' % va) == key]
        if not hit:
            raise SystemExit(
                'ABORT  --throttle-exclude %s: not a known %s slot function.\n'
                '       Give an FFNx symbol name or its 6-digit hex address; '
                'the names this build\n'
                '       knows are in ff7nx_dispatch_sites.py under '
                "DISPATCH_SITES['%s']['throttle'].\n"
                '       %d are already excluded, %d more can be moved across.'
                % (want, tag, tag, len(thr['exclude']),
                   len(thr.get('candidates', ()))))
        for name, va in hit:
            if va not in out:
                out.append(va)
                log('       throttle: also excluding %s (0x%06X) from %s'
                    % (name, va, tag))
    return out


def verify_throttle_untouched(text, dispatch, log=print):
    """
    Assert the call sites the throttle must NOT touch are still exactly where
    and what they were.

    execute_effect100_fn calls a slot function from two places. The second is
    the `else if (fn == display_battle_action_text_42782A)` arm, which FFNx runs
    undecorated. Throttling it would pause the action text for three frames in
    four and it would never advance. It is not hooked -- but "not hooked" is
    only meaningful if it is still there, so the stock word is checked. If a
    future build grew a third call site, ff7nx_locate.py would already have
    refused; this catches the input not being what that run saw.
    """
    n = 0
    for c in dispatch:
        thr = c.get('throttle')
        if not thr or c['kind'] != 'throttle_pre':
            continue
        for pc, want in zip(thr['undecorated'], thr['undecorated_words']):
            cur, = struct.unpack('<I', text[pc:pc + 4])
            if cur != want:
                raise SystemExit(
                    'ABORT  %s: the undecorated call site at +0x%X reads '
                    '%08X, expected %08X -- input is not stock, or the '
                    'dispatcher changed shape' % (c['tag'], pc, cur, want))
            log('  ok  +0x%06X  undecorated call site left alone  %s'
                % (pc, c['tag']))
            n += 1
    return n


def verify_dispatch_signature(text, c, log=print):
    """
    Re-check every stock word the site's identification depended on -- the
    address computation, the translator call, the load or store through x0, the
    slot guard and the guest-context spill of idx.

    The hook alone is not enough. The cave reads idx out of a guest register
    slot because one specific nearby instruction put it there; if that
    instruction moved, the hook word could still match while the cave read
    garbage and scaled the wrong slot. Verifying the window closes that.
    """
    hook = c['site']['hook']
    for rel, want in c['site']['sig']:
        off = hook + rel
        cur, = struct.unpack('<I', text[off:off + 4])
        if cur != want:
            raise SystemExit(
                'ABORT  %s\n       signature word at +0x%X (hook%+d)\n'
                '       expected %08X, found %08X -- input is not stock, or '
                'the site moved' % (c['label'], off, rel, want, cur))
    # Words a cave DEPENDS ON but does not sit next to. A hook signature only
    # proves the cave is being spliced into the right instruction; it says
    # nothing about the layout of the data the cave then goes and reads. Any
    # site may name absolute (address, word) pairs here and they are checked
    # exactly as strictly. `analog-360` uses this to pin the port's input
    # object layout, its DirectInput key buffer and the shape of the address
    # translator -- none of which are anywhere near either of its hooks.
    for addr, want, why in c['site'].get('assert', ()):
        cur, = struct.unpack('<I', text[addr:addr + 4])
        if cur != want:
            raise SystemExit(
                'ABORT  %s\n       %s\n'
                '       word at +0x%X expected %08X, found %08X -- input is '
                'not stock, or this build lays that out differently'
                % (c['label'], why, addr, want, cur))
    return len(c['site']['sig']) + len(c['site'].get('assert', ()))


# ---------------------------------------------------------------- exe (PE)

# --------------------------------------------------------------------------
# WHICH ff7_en IS THIS?
#
# Two x86 builds are in circulation and both run the game on the Switch:
#
#   pc-1.02        6,421,856 bytes  md5 c8886aeb0ff7ad500b05ad2f1ea8e059
#   switch-1.03_5  5,997,027 bytes  md5 ca7284c38d058f7c167a13e00fe72441
#
# The Switch one has been avoided on the strength of a note in build.py --
# "the Switch's own trimmed exe has everything at different addresses
# (verified by byte diff)". That is wrong, and it cost this project the whole
# Switch-exe route. A byte diff of two PE FILES compares FILE OFFSETS; these
# two differ in file alignment (raw 0x400 vs 0x200) and the Switch build drops
# the .FTS section, so of course the raw offsets move. What matters is the
# VIRTUAL layout, and there the two are the same image:
#
#   section   VA         vsize     identical bytes?
#   .text     0x00401000 0x3B5000  yes, all 0x3B4639 stored bytes
#   .rdata    0x007B6000 0x004000  yes, all 0x3CC0 stored bytes
#   .data     0x007BA000 0x797000  yes, all 0x1E2E06 stored bytes
#
# Same ImageBase, same entry point, same link timestamp. The PC build simply
# stores more trailing linker padding -- 455 zero bytes past the end of .text,
# 320 past .rdata, 506 past .data. Every VA this file patches is therefore
# valid in both, and the recompilation map inside `main` (keyed on x86 VAs)
# addresses both equally.
#
# The compatibility test is the CODE, not the file: sha1 of the first
# 0x3B4639 bytes of .text, the length both builds store. A future repack with
# different padding or section count still passes as long as the code it was
# derived from is unchanged.
TEXT_HASH_LEN = 0x3B4639
TEXT_SHA1 = '53744b569b93bc5ce96f4cc307ec065111f1a307'

EXE_BUILDS = {
    'c8886aeb0ff7ad500b05ad2f1ea8e059': 'pc-1.02',
    'ca7284c38d058f7c167a13e00fe72441': 'switch-1.03_5',
}


def pe_info(data):
    """Section table plus the header offsets needed to edit it."""
    pe = struct.unpack('<I', data[0x3c:0x40])[0]
    nsec = struct.unpack('<H', data[pe + 6:pe + 8])[0]
    optsz = struct.unpack('<H', data[pe + 20:pe + 22])[0]
    align = struct.unpack('<I', data[pe + 24 + 36:pe + 24 + 40])[0] or 0x200
    tbl = pe + 24 + optsz
    secs = []
    for i in range(nsec):
        s = data[tbl + 40 * i: tbl + 40 * (i + 1)]
        name = s[:8].rstrip(b'\0').decode('ascii', 'replace')
        vsize, va, rsize, raw = struct.unpack('<IIII', s[8:24])
        secs.append(dict(name=name, base=va + 0x400000, vsize=vsize,
                         rsize=rsize, raw=raw, hdr=tbl + 40 * i))
    return dict(sections=secs, file_align=align)


def pe_sections(data):
    return [(s['name'], s['base'], s['raw'], s['rsize'])
            for s in pe_info(data)['sections']]


def identify_exe(data, log=print):
    """Name the build and prove its code is the one every address came from."""
    md5 = hashlib.md5(data).hexdigest()
    name = EXE_BUILDS.get(md5)
    info = pe_info(data)
    text = next((s for s in info['sections'] if s['name'] == '.text'), None)
    if text is None:
        raise SystemExit('ABORT  no .text section -- not an FF7 exe')
    if text['rsize'] < TEXT_HASH_LEN:
        raise SystemExit('ABORT  .text stores only 0x%X bytes, need 0x%X'
                         % (text['rsize'], TEXT_HASH_LEN))
    got = hashlib.sha1(
        data[text['raw']:text['raw'] + TEXT_HASH_LEN]).hexdigest()
    if got != TEXT_SHA1:
        raise SystemExit(
            'ABORT  this exe\'s code is not the build these patches were '
            'derived from.\n'
            '       .text[0:0x%X] sha1 %s\n'
            '       expected       %s\n'
            '       Known-good: %s'
            % (TEXT_HASH_LEN, got, TEXT_SHA1,
               ', '.join('%s (%s)' % (v, k[:8]) for k, v in EXE_BUILDS.items())))
    log('exe build: %s  (md5 %s, %d bytes) -- code verified identical to the '
        'reference' % (name or 'unrecognised but code-identical', md5, len(data)))
    return name or 'unknown'


def ensure_raw_backed(data, va, n, log=print):
    """
    Make sure `n` bytes at `va` are backed by FILE bytes, growing a section's
    SizeOfRawData into existing zero padding if they are not.

    Why this is needed at all: the damage y-offset table is written into the
    linker alignment padding at the end of .rdata. The PC build stores that
    padding on disk; the Switch build stops 320 bytes earlier, so the exact
    same VA is inside .rdata's VirtualSize (it exists, zero-filled, at
    runtime) but has nowhere on disk to put the bytes.

    The fix does not move anything. Between the end of .rdata's raw data and
    the start of .data's there is already file-alignment padding -- 320 bytes,
    all zero, which is precisely the amount the PC build declares. Raising
    SizeOfRawData to cover it makes .rdata's on-disk extent identical to the
    PC build's, so both exes end up with the table at the same VA and the SAME
    NSO words repoint at it. No section moves, no offset shifts, nothing else
    in the file changes.

    Returns True if the header was edited.
    """
    info = pe_info(bytes(data))
    sec = next((s for s in info['sections']
                if s['base'] <= va < s['base'] + s['vsize']), None)
    if sec is None:
        raise SystemExit('ABORT  VA 0x%X is not inside any section' % va)
    rel = va - sec['base']
    if rel + n <= sec['rsize']:
        return False
    align = info['file_align']
    need = ((rel + n + align - 1) // align) * align
    if need > sec['vsize']:
        raise SystemExit(
            'ABORT  VA 0x%X +%d is past %s\'s VirtualSize (0x%X); there is no '
            'such address at runtime' % (va, n, sec['name'], sec['vsize']))
    later = [s['raw'] for s in info['sections'] if s['raw'] > sec['raw']]
    limit = min(later) if later else len(data)
    if sec['raw'] + need > limit:
        raise SystemExit(
            'ABORT  %s needs SizeOfRawData 0x%X to reach VA 0x%X, but the next '
            'section starts at file 0x%X.\n'
            '       Growing it would have to move data, which this tool will '
            'not do.' % (sec['name'], need, va, limit))
    slack = bytes(data[sec['raw'] + sec['rsize']:sec['raw'] + need])
    if set(slack) - {0}:
        raise SystemExit(
            'ABORT  the file padding after %s (file 0x%X..0x%X) is not zero, '
            'so it is not padding.\n       Refusing to claim it.'
            % (sec['name'], sec['raw'] + sec['rsize'], sec['raw'] + need))
    if len(data) < sec['raw'] + need:
        data.extend(b'\0' * (sec['raw'] + need - len(data)))
    struct.pack_into('<I', data, sec['hdr'] + 16, need)
    log('  ..  %s SizeOfRawData 0x%X -> 0x%X (claiming %d zero byte(s) of '
        'existing file padding so VA 0x%X is writable)'
        % (sec['name'], sec['rsize'], need, need - sec['rsize'], va))
    return True


def va_to_off(sections, va):
    for name, base, raw, size in sections:
        if base <= va < base + size:
            return raw + (va - base), name
    raise ValueError('VA 0x%X is not inside any raw section' % va)


def patch_exe(data, patches, verify_only=False, log=print):
    data = bytearray(data)
    for label, va, old, new in patches:
        # Grow a section's raw extent if, and only if, this patch needs bytes
        # the file does not store. On the PC build this never fires.
        if not verify_only:
            ensure_raw_backed(data, va, len(new), log)
        sec = pe_sections(bytes(data))
        try:
            off, sname = va_to_off(sec, va)
        except ValueError:
            raise SystemExit(
                'ABORT  %s\n       VA 0x%X has no file bytes in this exe '
                'build.\n       (--verify does not grow sections; run without '
                'it to see whether it can be made writable.)' % (label, va))
        cur = bytes(data[off:off + len(old)])
        if cur != old:
            # ALREADY AT THE TARGET VALUE is not a mismatch, it is a no-op.
            #
            # The base exe is not always pristine: 7th Heaven NX bakes any
            # HEXT packs the enabled mods ship before this runs, and the
            # "60/30 FPS Gameplay" mod's flag.txt sets guest 0x914B21 to 1 --
            # byte for byte the same thing as this file's "60fps mod
            # compatibility flag". Treating that as "input is not stock" fails
            # a build whose exe is already exactly what we were going to write.
            #
            # Only an exact match on the full replacement counts. Anything
            # else -- including a partial match -- is still a hard abort,
            # because the whole point of verifying is that a wrong base exe
            # must fail loudly rather than get half-patched.
            if cur == new:
                log('  --  VA 0x%06X %-8s %s  (already set -- HEXT or an '
                    'earlier pass wrote the same value)' % (va, sname, label))
                continue
            raise SystemExit(
                'ABORT  %s\n       VA 0x%X (%s, file 0x%X)\n'
                '       expected %s, found %s -- input is not stock?'
                % (label, va, sname, off, old.hex(' '), cur.hex(' ')))
        if not verify_only:
            data[off:off + len(new)] = new
        log('  ok  VA 0x%06X %-8s %s' % (va, sname, label))
    return bytes(data)


# ---------------------------------------------------------------- main (NSO)

def nso_segments(data):
    segs = [struct.unpack('<III', data[b:b + 12]) for b in (0x10, 0x20, 0x30)]
    comp = struct.unpack('<III', data[0x60:0x6c])
    flags = struct.unpack('<I', data[0xc:0x10])[0]
    raw = []
    for i, (fo, mo, ds) in enumerate(segs):
        blob = data[fo:fo + comp[i]]
        raw.append(lz4.block.decompress(blob, uncompressed_size=ds)
                   if flags & (1 << i) else blob[:ds])
    return segs, raw


def patch_nso(data, patches, verify_only=False, log=print, extra=(),
              throttle=0, nframes=0, scalers=(), scaler_mult=2,
              dispatch=(), batt_mult=4, nso_path=None):
    global _BSS_GROW
    # --cam-throttle and the dispatcher groups both want scratch at the end of
    # BSS and neither knows about the other's allocation, so they would overlap.
    # --cam-throttle is DO-NOT-USE anyway (it skips a translated function and
    # crashes), but an accidental combination would corrupt memory silently,
    # which is worse than crashing.
    if dispatch and throttle:
        raise SystemExit('ABORT  --cam-throttle and the dispatcher groups both '
                         'allocate at the end of BSS and would overlap.\n'
                         '       --cam-throttle is known-broken; drop it.')
    if data[:4] != b'NSO0':
        raise SystemExit('ABORT  not an NSO (magic %r)' % data[:4])
    bid = data[0x40:0x50].hex().upper()
    if bid != BUILD_ID:
        raise SystemExit('ABORT  build ID %s, expected %s\n'
                         '       wrong version -- dump the 1.0.3 update exefs'
                         % (bid, BUILD_ID))
    segs, raw = nso_segments(data)
    for i in range(3):
        if hashlib.sha256(raw[i]).digest() != data[0xA0 + 32 * i:0xA0 + 32 * i + 32]:
            raise SystemExit('ABORT  segment %d hash mismatch -- corrupt input' % i)
    text = bytearray(raw[0])
    # Two groups may legitimately want the same word changed the same way --
    # `script-wait` and `r-battle_aura` both resolve +0x833800 to 14 -> 56, by
    # different routes. That is corroboration, not a conflict: apply it once.
    # Two groups wanting the same word changed DIFFERENTLY is a real bug, and
    # would otherwise show up only as a confusing verify failure on the second.
    seen = {}
    todo = []
    for label, off, old, new in list(patches) + list(extra):
        if off in seen:
            plabel, pold, pnew = seen[off]
            if (pold, pnew) == (old, new):
                log('  --  +0x%06X  already covered by "%s", skipping "%s"'
                    % (off, plabel, label))
                continue
            raise SystemExit(
                'ABORT  two enabled patches disagree about module offset 0x%X:\n'
                '       %s wants %08X -> %08X\n'
                '       %s wants %08X -> %08X\n'
                '       these groups cannot be enabled together'
                % (off, plabel, pold, pnew, label, old, new))
        seen[off] = (label, old, new)
        todo.append((label, off, old, new))
    for label, off, old, new in todo:
        cur, = struct.unpack('<I', text[off:off + 4])
        if cur != old:
            raise SystemExit(
                'ABORT  %s\n       module offset 0x%X\n'
                '       expected %08X, found %08X -- input is not stock?'
                % (label, off, old, cur))
        if not verify_only:
            struct.pack_into('<I', text, off, new)
        log('  ok  +0x%06X  %08X -> %08X  %s' % (off, old, new, label))

    # --- code caves -------------------------------------------------------
    ro_base = segs[1][1]
    for c in NSO_CAVES:
        cur, = struct.unpack('<I', text[c['hook']:c['hook'] + 4])
        if cur != c['orig']:
            raise SystemExit(
                'ABORT  %s\n       hook offset 0x%X\n'
                '       expected %08X, found %08X -- input is not stock?'
                % (c['label'], c['hook'], c['orig'], cur))
        if verify_only:
            log('  ok  +0x%06X  cave hook            %s' % (c['hook'], c['label']))
            continue
        cave = len(text)
        words = []
        for w in c['body']:
            if w is ORIG:
                words.append(c['orig'])
            elif w is BACK:
                pc = cave + 4 * len(words)
                words.append(0x14000000 | (((c['hook'] + 4 - pc) >> 2) & 0x3FFFFFF))
            else:
                words.append(w)
        text.extend(struct.pack('<%dI' % len(words), *words))
        if len(text) > ro_base:
            raise SystemExit('ABORT  cave overflows into .rodata')
        struct.pack_into('<I', text, c['hook'],
                         0x14000000 | (((cave - c['hook']) >> 2) & 0x3FFFFFF))
        log('  ok  +0x%06X  -> cave 0x%06X (%d words)  %s'
            % (c['hook'], cave, len(words), c['label']))
    # --- field walk/run step tick-gate (see comment block above) ----------
    build_walk_gate_caves(text, ro_base, segs, data, verify_only, log)

    # --- battle attack movement delta scaling (see comment block above) ---
    if any(c.get('tag') == 'effect10' for c in dispatch):
        bmv_shift = {2: 1, 4: 2, 8: 3}.get(batt_mult)
        if bmv_shift is None:
            raise SystemExit('ABORT  battle multiplier must be 2, 4 or 8')
        build_battle_move_caves(text, ro_base, verify_only, log, bmv_shift)

    # --- post-call return-value scalers ----------------------------------
    shift = {2: 1, 4: 2, 8: 3}.get(scaler_mult)
    if scalers and shift is None:
        raise SystemExit('ABORT  scaler multiplier must be 2, 4 or 8')
    for site in scalers:
        cur, = struct.unpack('<I', text[site['hook']:site['hook'] + 4])
        if cur != site['displaced']:
            raise SystemExit(
                'ABORT  opcode scaler %s\n       hook +0x%X\n'
                '       expected %08X, found %08X -- input is not stock?'
                % (site['name'], site['hook'], site['displaced'], cur))
        # Re-derive the context register from the instruction we are about to
        # displace, rather than trusting the recorded value.
        if (cur & ~(0x1F << 5)) != 0xB9401008:
            raise SystemExit(
                'ABORT  opcode scaler %s: displaced insn %08X is not '
                '`ldr w8,[ctx,#0x10]`' % (site['name'], cur))
        if ((cur >> 5) & 0x1F) != site['ctx']:
            raise SystemExit(
                'ABORT  opcode scaler %s: context register is x%d, table says '
                'x%d' % (site['name'], (cur >> 5) & 0x1F, site['ctx']))
        if verify_only:
            log('  ok  +0x%06X  scaler hook   %s %s x%d'
                % (site['hook'], site['name'], site['op'], site['ctx']))
            continue
        cave = len(text)
        words = opcode_scaler_cave(cave, site, scaler_mult, shift)
        text.extend(struct.pack('<%dI' % len(words), *words))
        if len(text) > ro_base:
            raise SystemExit('ABORT  opcode scaler cave overflows .rodata')
        struct.pack_into('<I', text, site['hook'],
                         0x14000000 | (((cave - site['hook']) >> 2) & 0x3FFFFFF))
        log('  ok  +0x%06X  -> cave 0x%06X (%2d words)  %s %s x%d  %s'
            % (site['hook'], cave, len(words), site['name'],
               '*%d' % scaler_mult if site['op'] == 'mul' else '/%d' % scaler_mult,
               site['ctx'], site['what']))

    # --- battle effect / camera dispatcher first-frame scalers -----------
    #
    # Verified first, ALL of them, before any of them is written. A partially
    # applied dispatcher group is the worst possible state: add_fn would set a
    # flag that the dispatcher never consumes, or the dispatcher would read a
    # flag nothing ever sets. Either way the result is silently half-scaled
    # battle timing, which is exactly the class of bug that made previous test
    # results uninterpretable.
    for c in dispatch:
        n = verify_dispatch_signature(text, c, log)
        if verify_only:
            log('  ok  +0x%06X  dispatcher hook  %-42s (%d stock word(s) '
                'verified)' % (c['site']['hook'], c['label'], n))
    verify_throttle_untouched(text, dispatch, log)
    hooks = [c['site']['hook'] for c in dispatch]
    if len(set(hooks)) != len(hooks):
        dup = sorted(h for h in set(hooks) if hooks.count(h) > 1)
        raise SystemExit('ABORT  two dispatcher caves want the same hook: %s\n'
                         '       the second would overwrite the first\'s branch'
                         % ', '.join('+0x%X' % h for h in dup))
    if dispatch and not verify_only:
        dispatcher_entries = [c for c in dispatch if c['kind'] == 'dispatcher']
        use_shared = _sharedp is not None and len(dispatcher_entries) >= 2

        if use_shared:
            rodata_va, data_va = segs[1][1], segs[2][1]
            table_base_va = rodata_va + len(raw[1])
            needed = 12 * len(dispatcher_entries)
            gap = data_va - table_base_va
            if gap < needed:
                raise SystemExit(
                    'ABORT  shared-prologue descriptor table needs %d bytes '
                    'in the .rodata tail gap (0x%X..0x%X), only %d '
                    'available before .data begins at 0x%X. This should '
                    'not happen on a stock 1.0.3 NSO -- input may already '
                    'be modified.' % (needed, table_base_va, table_base_va
                                      + gap, gap, data_va))
            shift = {2: 1, 4: 2, 8: 3}[batt_mult]
            entry_va = {}
            table_bytes = bytearray()
            for i, c in enumerate(dispatcher_entries):
                d = DISPATCH_SITES[c['tag']]
                entry_va[c['tag']] = table_base_va + 12 * i
                table_bytes += _sharedp.pack_table_entry(
                    d['flag'], entry_va[c['tag']], d['data_base'],
                    d['stride'])
            raw[1] = raw[1] + bytes(table_bytes)   # claim the verified,
            # already-mapped, R-- tail of .rodata's own last page -- .data's
            # VA is a fixed header field, independent of .rodata's declared
            # size, so this cannot move or shrink anything else.

            shared_va = len(text)
            shared_words = _sharedp.build_shared_prologue(shared_va)
            text.extend(struct.pack('<%dI' % len(shared_words),
                                    *shared_words))
            if len(text) > ro_base:
                raise SystemExit(
                    'ABORT  shared-prologue body overflows into .rodata')
            log('  ok  shared-prologue body -> cave 0x%06X (%d words), '
                'descriptor table -> .rodata +0x%X (%d bytes, %d site(s))'
                % (shared_va, len(shared_words), table_base_va - rodata_va,
                   len(table_bytes), len(dispatcher_entries)))

            for c in dispatcher_entries:
                d = DISPATCH_SITES[c['tag']]
                sym = {n: va for n, va, _v, _g in d['cases']}
                cases = [(n, s, g) for n, _v, s, g in d['cases']]
                # selfcheck_nso later re-derives `tgt` from the shipped hook
                # branch and calls c['build'](tgt) to compare against what
                # actually got written -- it reuses this SAME `dispatch`
                # list (same dict objects), so replacing c['build'] here
                # makes it verify the mechanism that actually shipped,
                # instead of the old inline one.
                c['build'] = (lambda cave, d=d, sym=sym, cases=cases,
                              ev=entry_va[c['tag']], sv=shared_va,
                              site=c['site'], shift=shift:
                              _sharedp.build_cave_shared(
                                  cave, site, d['mask_bits'], ev, sv,
                                  d['data_base'], d['stride'], d['fields'],
                                  cases, sym, shift))

            remaining = [c for c in dispatch if c['kind'] != 'dispatcher']
            placed_label_suffix = '  [shared prologue]'
        else:
            remaining = dispatch
            placed_label_suffix = ''

        # Caves that go in reclaimed alignment padding are placed separately,
        # after the tail gap is finished -- see _place_padding_caves.
        padded = [c for c in remaining if c['site'].get('place') == 'padding']
        remaining = [c for c in remaining
                     if c['site'].get('place') != 'padding']

        for c in (dispatcher_entries if use_shared else []) + remaining:
            cave = len(text)
            words = c['build'](cave)
            text.extend(struct.pack('<%dI' % len(words), *words))
            if len(text) > ro_base:
                raise SystemExit(
                    'ABORT  dispatcher cave overflows .rodata (%d bytes over) '
                    '-- enable fewer groups.\n'
                    '       .text ends at 0x%X and .rodata begins at 0x%X, so '
                    'the caves have %d bytes\n'
                    '       to share. effect10-scale alone is 760 of them; '
                    'each *-throttle group is about 350.'
                    % (len(text) - ro_base, len(raw[0]), ro_base,
                       ro_base - len(raw[0])))
            struct.pack_into('<I', text, c['site']['hook'],
                             0x14000000 | (((cave - c['site']['hook']) >> 2)
                                           & 0x3FFFFFF))
            suffix = placed_label_suffix if (use_shared and c['kind']
                                             == 'dispatcher') else ''
            log('  ok  +0x%06X  -> cave 0x%06X (%3d words)  %s%s'
                % (c['site']['hook'], cave, len(words), c['label'], suffix))
        if padded:
            _place_padding_caves(padded, text, raw, segs, nso_path, log)
        if any(c.get('throttle') for c in dispatch):
            _BSS_GROW = max(_BSS_GROW, BSS_GROW_THROTTLE)
            log('      flag block at module +0x%X, throttle block at +0x%X, '
                'bssSize grown by 0x%X'
                % (FLAG_BASE, THROTTLE_BASE, BSS_GROW_THROTTLE))
        else:
            _BSS_GROW = max(_BSS_GROW, BSS_GROW)
            log('      flag block at module +0x%X, bssSize grown by 0x%X'
                % (FLAG_BASE, BSS_GROW))
        if any(c['tag'] == ANALOG_GROUP for c in dispatch):
            # The 360-movement scratch sits AFTER the throttle block, so a
            # build without this group keeps the bss growth it always had and
            # still reproduces its old md5 exactly.
            import ff7nx_analog as _AN
            _BSS_GROW = max(_BSS_GROW, _AN.ANALOG_GROW)
            log('      360 movement scratch at module +0x%X, bssSize grown '
                'by 0x%X' % (_AN.ANALOG_BASE, _AN.ANALOG_GROW))

    if nframes and not verify_only:
        shift = {2: 1, 4: 2}.get(nframes)
        if shift is None:
            raise SystemExit('ABORT  --cam-nframes must be 2 or 4')
        for hook in CAM_NFRAMES_SITES:
            cur, = struct.unpack('<I', text[hook:hook + 4])
            if cur != CAM_NFRAMES_STRH:
                raise SystemExit('ABORT  n_frames site 0x%X: expected %08X, got %08X'
                                 % (hook, CAM_NFRAMES_STRH, cur))
            cave = len(text)
            words = _nframes_cave(cave, cur, hook, shift)
            text.extend(struct.pack('<%dI' % len(words), *words))
            if len(text) > ro_base:
                raise SystemExit('ABORT  n_frames cave overflows .rodata')
            struct.pack_into('<I', text, hook,
                             0x14000000 | (((cave - hook) >> 2) & 0x3FFFFFF))
            log('  ok  +0x%06X  -> cave 0x%06X  camera n_frames x%d'
                % (hook, cave, nframes))
    if throttle and not verify_only:
        mask = {2: 0, 4: 1}.get(throttle)
        if mask is None:
            raise SystemExit('ABORT  --cam-throttle must be 2 or 4')
        # BSS starts at the PAGE-ALIGNED end of .data, not its raw end.
        # Using the raw end put the counters 0x328 bytes inside live BSS and
        # crashed on entering battle.
        bss = struct.unpack('<I', data[0x3C:0x40])[0]
        data_end = (segs[2][1] + segs[2][2] + 0xFFF) & ~0xFFF
        base_ctr = data_end + bss
        for n, (label, hook, orig_expect) in enumerate(CAM_FNS):
            cur, = struct.unpack('<I', text[hook:hook + 4])
            if cur != orig_expect:
                raise SystemExit('ABORT  camera fn %s: expected %08X, got %08X'
                                 % (label, orig_expect, cur))
            counter = base_ctr + 4 * n
            cave = len(text)
            words = _cam_throttle_cave(cave, counter, cur, mask, hook)
            text.extend(struct.pack('<%dI' % len(words), *words))
            if len(text) > ro_base:
                raise SystemExit('ABORT  camera cave overflows .rodata')
            struct.pack_into('<I', text, hook,
                             0x14000000 | (((cave - hook) >> 2) & 0x3FFFFFF))
            log('  ok  +0x%06X  -> cave 0x%06X ctr 0x%X  camera %s 1-in-%d'
                % (hook, cave, counter, label, throttle))
        _BSS_GROW = 4 * len(CAM_FNS) + 4
    if verify_only:
        return None
    log('  .text %d bytes, %d free before .rodata' % (len(text), ro_base - len(text)))
    raw[0] = bytes(text)

    out = bytearray(data[:0x100])
    body = b''
    fo = 0x100
    for i in range(3):
        comp = lz4.block.compress(raw[i], mode='high_compression',
                                  compression=12, store_size=False)
        struct.pack_into('<I', out, 0x10 + 16 * i, fo)           # file offset
        struct.pack_into('<I', out, 0x18 + 16 * i, len(raw[i]))  # decomp size
        struct.pack_into('<I', out, 0x60 + 4 * i, len(comp))     # comp size
        out[0xA0 + 32 * i:0xA0 + 32 * i + 32] = hashlib.sha256(raw[i]).digest()
        body += comp
        fo += len(comp)
    if _BSS_GROW > 0:
        bss = struct.unpack('<I', out[0x3C:0x40])[0]
        struct.pack_into('<I', out, 0x3C, bss + _BSS_GROW)
    return bytes(out) + body


def selfcheck_nso(blob, patches, log=print, extra=(), dispatch=(), batt_mult=4):
    """Re-parse the output exactly as the loader would."""
    segs, raw = nso_segments(blob)
    ok = True
    for i in range(3):
        want = blob[0xA0 + 32 * i:0xA0 + 32 * i + 32]
        if hashlib.sha256(raw[i]).digest() != want:
            log('  !! segment %d sha256 mismatch' % i)
            ok = False
    for label, off, old, new in list(patches) + list(extra):
        cur, = struct.unpack('<I', raw[0][off:off + 4])
        if cur != new:
            log('  !! +0x%X reads %08X, wanted %08X' % (off, cur, new))
            ok = False
    # Each dispatcher hook must now be an unconditional B into .text, and the
    # cave it lands in must start with the instruction the cave is supposed to
    # start with -- for add_fn caves the idx load, for dispatcher caves the same.
    for c in dispatch:
        hook = c['site']['hook']
        cur, = struct.unpack('<I', raw[0][hook:hook + 4])
        if (cur >> 26) != 0x05:
            log('  !! dispatcher hook +0x%X is not a branch (%08X)'
                % (hook, cur))
            ok = False
            continue
        disp = cur & 0x3FFFFFF
        if disp & 0x2000000:
            disp -= 0x4000000
        tgt = hook + disp * 4
        if not (0 <= tgt < len(raw[0])):
            log('  !! dispatcher hook +0x%X branches outside .text' % hook)
            ok = False
            continue
        if c.get('placed') is not None:
            # A cave chained through padding is not contiguous at `tgt`, so it
            # is checked word by word against the layout that was actually
            # emitted -- every one of them, including the `b`s between runs.
            bad = [va for va, w in c['placed'].items()
                   if struct.unpack_from('<I', raw[0], va)[0] != w]
            if tgt != min(c['placed']) and tgt not in c['placed']:
                log('  !! %s: hook branches to 0x%X, which is not in its cave'
                    % (c['label'], tgt))
                ok = False
            if bad:
                log('  !! cave for %s differs at %d of %d word(s): %s'
                    % (c['label'], len(bad), len(c['placed']),
                       ['0x%X' % b for b in sorted(bad)[:6]]))
                ok = False
            continue
        want = c['build'](tgt)
        got = struct.unpack('<%dI' % len(want), raw[0][tgt:tgt + 4 * len(want)])
        if list(got) != list(want):
            bad = [i for i, (a, b) in enumerate(zip(got, want)) if a != b]
            log('  !! cave for %s differs from what was asked for at word(s) %s'
                % (c['label'], bad[:8]))
            ok = False
    for c in NSO_CAVES:
        cur, = struct.unpack('<I', raw[0][c['hook']:c['hook'] + 4])
        if (cur >> 26) != 0x05:                       # unconditional B
            log('  !! hook +0x%X is not a branch (%08X)' % (c['hook'], cur))
            ok = False
            continue
        disp = cur & 0x3FFFFFF
        if disp & 0x2000000:
            disp -= 0x4000000
        tgt = c['hook'] + disp * 4
        if not (0 <= tgt < len(raw[0])):
            log('  !! hook +0x%X branches outside .text' % c['hook'])
            ok = False
            continue
        first, = struct.unpack('<I', raw[0][tgt:tgt + 4])
        if first != c['orig']:
            log('  !! cave at 0x%X does not begin with the displaced instruction' % tgt)
            ok = False
    for hook, orig, label in ((WALK_TICK_HOOK, WALK_TICK_ORIG, 'field tick counter'),
                              (WALK_PFLAG_SET_HOOK, WALK_PFLAG_SET_ORIG, 'field walk/run step, player-flag set'),
                              (WALK_PFLAG_CLR_HOOK, WALK_PFLAG_CLR_ORIG, 'field walk/run step, player-flag clear'),
                              (WALK_X_HOOK, WALK_X_ORIG, 'field walk/run step (X)'),
                              (WALK_Y_HOOK, WALK_Y_ORIG, 'field walk/run step (Y)')):
        cur, = struct.unpack('<I', raw[0][hook:hook + 4])
        if (cur >> 26) != 0x05:
            log('  !! %s hook +0x%X is not a branch (%08X)' % (label, hook, cur))
            ok = False
            continue
        disp = cur & 0x3FFFFFF
        if disp & 0x2000000:
            disp -= 0x4000000
        tgt = hook + disp * 4
        if not (0 <= tgt < len(raw[0])):
            log('  !! %s hook +0x%X branches outside .text' % (label, hook))
            ok = False
            continue
        first, = struct.unpack('<I', raw[0][tgt:tgt + 4])
        if first != orig:
            log('  !! %s cave at 0x%X does not begin with the displaced instruction'
                % (label, tgt))
            ok = False
    if any(c.get('tag') == 'effect10' for c in dispatch):
        bmv_shift = {2: 1, 4: 2, 8: 3}.get(batt_mult)
        # patch_nso already aborted on a bad batt_mult before this point if
        # the group is active, so bmv_shift is never None here -- but refuse
        # rather than silently skip the check if that ever stops being true.
        if bmv_shift is None:
            log('  !! battle attack movement: battle multiplier must be 2, 4 or 8')
            ok = False
        else:
            for hook, orig, label, build in (
                    (BATTLE_MOVE_X_HOOK, BATTLE_MOVE_X_ORIG,
                     'battle attack movement, delta (X)',
                     lambda cave: _battle_move_delta_cave(cave, BATTLE_MOVE_X_HOOK, bmv_shift)),
                    (BATTLE_MOVE_Z_HOOK, BATTLE_MOVE_Z_ORIG,
                     'battle attack movement, delta (Z)',
                     lambda cave: _battle_move_delta_cave(cave, BATTLE_MOVE_Z_HOOK, bmv_shift)),
                    (BATTLE_MOVE_Y_HOOK, BATTLE_MOVE_Y_ORIG,
                     'battle attack movement, delta (Y)',
                     lambda cave: _battle_move_delta_cave(cave, BATTLE_MOVE_Y_HOOK, bmv_shift)),
                    (BATTLE_MOVE_YIDX_HOOK, BATTLE_MOVE_YIDX_ORIG,
                     'battle attack movement, Y-lookup index',
                     lambda cave: _battle_move_yidx_cave(cave, BATTLE_MOVE_YIDX_HOOK, bmv_shift))):
                cur, = struct.unpack('<I', raw[0][hook:hook + 4])
                if (cur >> 26) != 0x05:
                    log('  !! %s hook +0x%X is not a branch (%08X)' % (label, hook, cur))
                    ok = False
                    continue
                disp = cur & 0x3FFFFFF
                if disp & 0x2000000:
                    disp -= 0x4000000
                tgt = hook + disp * 4
                if not (0 <= tgt < len(raw[0])):
                    log('  !! %s hook +0x%X branches outside .text' % (label, hook))
                    ok = False
                    continue
                want = build(tgt)
                got = struct.unpack('<%dI' % len(want), raw[0][tgt:tgt + 4 * len(want)])
                if list(got) != list(want):
                    bad = [i for i, (g, wv) in enumerate(zip(got, want)) if g != wv]
                    log('  !! %s cave at 0x%X differs from what was built at word(s) %s'
                        % (label, tgt, bad[:8]))
                    ok = False
    return ok


# --------------------------------------------------------------------------
# THE SHIPPING PRESET
#
# One definition of "the 60 FPS patches", used by the GUI checkbox and by the
# documented command line, so the two can never drift. Adding a group means
# editing this tuple and nothing else.
#
# Everything here has been through a hardware test. Groups deliberately left
# out: `nfade` and `scale-vwoft` (see FINDINGS-2 -- VWOFT writes a byte and is
# the one scroll opcode that can produce a wrong value rather than a slower
# move), `effect60-throttle`, and every `p-`/`c-` group.
RECOMMENDED_ENABLE = (
    # battle effect/camera dispatchers
    'effect10-scale', 'effect100-scale', 'camera-scale',
    'effect100-throttle', 'aura-throttle', 'camera-wait',
    # battle constants
    'victory', 'victory-fade', 'damage-numbers', 'limit-aura', 'aura-eskill',
    'aura-summon', 'boss-death', 'battle-text', 'tifa-slots',
    # field
    'field-wait', 'opcode-scale-safe', 'field-text',
    'field-blink', 'field-blink-hold',
)
RECOMMENDED_DISABLE_SYM = ('battle_sub_42A72D',)


def recommended_argv(out, exe=None, nso=None, dump=None, battle_lgp=None,
                     exe_identity=None):
    """The argv the shipping preset corresponds to."""
    argv = ['--out', out, '--legacy', '--enable-all']
    if dump:
        argv += ['--dump', dump]
    if exe:
        argv += ['--exe', exe]
    if exe_identity:
        argv += ['--exe-identity', exe_identity]
    if nso:
        argv += ['--nso', nso]
    for sym in RECOMMENDED_DISABLE_SYM:
        argv += ['--disable-sym', sym]
    argv += ['--enable', ','.join(RECOMMENDED_ENABLE)]
    if battle_lgp:
        argv += ['--battle-lgp', battle_lgp]
    return argv


def run(argv, log=print):
    """
    Run a build in-process and send its output to `log`.

    Exists so the GUI can reuse this file rather than re-implement it or shell
    out to it. Module state that main() accumulates is reset first, because a
    long-lived GUI process may build several times and `_BSS_GROW` is a
    running maximum -- a second, smaller build would otherwise inherit the
    first one's BSS growth.

    Returns True on success. A SystemExit from any of the ABORT paths is
    caught and its message logged, so a refusal reads as a failed step
    instead of killing the application.
    """
    global _BSS_GROW
    import contextlib
    import io

    saved_argv = sys.argv
    _BSS_GROW = 0
    buf = io.StringIO()
    try:
        sys.argv = ['ff7nx_60fps.py'] + list(argv)
        with contextlib.redirect_stdout(buf):
            rc = main()
    except SystemExit as exc:
        code = exc.code
        rc = 0 if code is None or code == 0 else 1
        if rc:
            buf.write('\n%s\n' % code)
    except Exception as exc:                       # noqa: BLE001
        rc = 1
        buf.write('\n%s: %s\n' % (type(exc).__name__, exc))
    finally:
        sys.argv = saved_argv
        _BSS_GROW = 0
        for line in buf.getvalue().splitlines():
            log(line)
    return rc == 0


# ---------------------------------------------------------------- driver

def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dump', metavar='DIR',
                    help='a Switch game dump; --exe and --nso are taken from '
                         'it (romfs/ff7/resources/ff7_1.02/ff7_en and '
                         'exefs/main) unless given explicitly')
    ap.add_argument('--exe', help='stock ff7_en (from romfs)')
    ap.add_argument('--exe-identity', metavar='PATH',
                    help='the exe to VERIFY against TEXT_SHA1, when --exe has '
                         'already had HEXT baked into it. Patching still '
                         'targets --exe; only the identity check reads this. '
                         'See identify_exe.')
    ap.add_argument('--nso', help='stock exefs/main (1.0.3)')
    ap.add_argument('--out', default='./sdout', help='SD tree output dir')
    ap.add_argument('--title-id', default=TITLE_ID)
    ap.add_argument('--camdat', metavar='DIR',
                    help='folder holding camdat0/1/2.bin '
                         '(workingdir/data/lang-en/battle)')
    ap.add_argument('--cam-mult', type=int, default=4, metavar='N',
                    help='battle camera wait multiplier (default 4; '
                         'battle is natively 15 FPS, so 60 FPS = 4)')
    ap.add_argument('--cam-step', type=int, default=0, metavar='N',
                    help='ALSO multiply the camera step divisor by N (2/4/8) '
                         'in main. Scales the same quantity as --cam-mult, so '
                         'use one or the other, not both.')
    ap.add_argument('--battle-lgp', metavar='LGP',
                    help='your BUILT battle.lgp (from the 7th Heaven NX '
                         'sdout); scales battle animation script waits')
    ap.add_argument('--anim-mult', type=int, default=4, metavar='N',
                    help='battle animation script wait multiplier '
                         '(default 4; battle is natively 15 FPS)')
    ap.add_argument('--cam-throttle', type=int, default=0, metavar='N',
                    help='run the battle camera dispatcher 1 frame in N '
                         '(2 or 4). Fixes battle-start, magic, limit-break '
                         'and victory camera pacing in one hook.')
    ap.add_argument('--limiter-fps', type=float, default=0, metavar='N',
                    help='what the busy-wait frame limiters aim for, in FPS. '
                         'Default 0 = leave at 60. The display is paced by '
                         'vsync at 60 either way; aiming higher only stops '
                         'the limiter from being the last thing before the '
                         'vsync deadline. See frame_pacing_note().')
    ap.add_argument('--cam-nframes', type=int, default=0, metavar='N',
                    help='multiply battle camera move durations by N (2 or 4). '
                         'This is FFNx\'s actual fix -- scales n_frames only.')
    ap.add_argument('--verify', action='store_true',
                    help='check inputs are stock and patchable; write nothing')
    ap.add_argument('--enable', action='append', default=[], metavar='GROUP',
                    help='enable an unverified patch group (repeatable, or '
                         'comma-separated). Use --list-groups to see them.')
    ap.add_argument('--enable-all', action='store_true',
                    help='enable every group -- the full FFNx constant set. '
                         'This is what you want for "does all of it help?". '
                         'If the answer is no, bisect with --disable.')
    ap.add_argument('--disable', action='append', default=[], metavar='GROUP',
                    help='remove a group from --enable-all (repeatable, or '
                         'comma-separated)')
    ap.add_argument('--disable-sym', action='append', default=[], metavar='SYM',
                    help='drop every patch belonging to one FFNx symbol, '
                         'keeping the rest of its group. Bisect a bad group '
                         'without losing the patches that work.')
    ap.add_argument('--movie-ratio', type=int, default=2, metavar='N',
                    help='how many times the frame rate of the movies in '
                         'data/movies exceeds vanilla 15 fps. Only used by '
                         'the %s group.' % MOVIE_FPS_GROUP)
    ap.add_argument('--scaler-mult', type=int, default=2, metavar='N',
                    help='multiplier for the post-call opcode scalers '
                         '(default 2 = FFNx common_frame_multiplier at 60 FPS)')
    ap.add_argument('--force-lgp', action='store_true',
                    help='scale battle.lgp even if it looks already scaled. '
                         'Only if you are certain the source is stock.')
    ap.add_argument('--throttle-exclude', action='append', default=[],
                    metavar='SYM',
                    help='exclude one more slot function from the pause-'
                         'throttle (repeatable, or comma-separated). Takes an '
                         'FFNx symbol name or its 6-digit hex address. This is '
                         'the bisect lever for *-throttle: the policy is '
                         'throttle-unless-named, so a misbehaving effect is '
                         'found by moving suspects across, not by turning the '
                         'group off.')
    ap.add_argument('--throttle-only', action='append', default=[],
                    metavar='ADDR',
                    help='add an effect60 slot function to the aura-throttle '
                         'ALLOW list (repeatable, or comma-separated). Takes a '
                         '6-digit hex guest address. `--list-effect60` prints '
                         'every candidate. This is the lever for finding an '
                         'effect that is still running at full rate: FFNx '
                         'throttles 71 of the 91 effect60 slots, aura-throttle '
                         'ships 2.')
    ap.add_argument('--list-effect60', action='store_true',
                    help='list every effect60 slot function and whether FFNx '
                         'runs it at full rate or throttles it, then exit')
    ap.add_argument('--batt-mult', type=int, default=4, metavar='N',
                    help='battle_frame_multiplier for the dispatcher scalers '
                         '(default 4; battle is natively 15 FPS, so 60 = 4)')
    ap.add_argument('--legacy', action='store_true',
                    help='reproduce the previous session\'s build exactly -- '
                         'the only patch set with real hardware history. Use '
                         'this as the baseline for every comparison.')
    ap.add_argument('--list-groups', action='store_true',
                    help='list the patch groups and exit')
    a = ap.parse_args()

    if a.list_effect60:
        t = (DISPATCH_SITES.get('effect60') or {}).get('throttle') or {}
        allow = {va for _n, va in t.get('aura_allow', ())}
        named = {va for _n, va in t.get('exclude', ())}
        print('effect60 slot functions (from add_fn_to_effect60_fn call sites)')
        print('  FFNx runs %d at full rate and throttles the rest.' % len(named))
        print('  aura-throttle currently throttles: %s'
              % ', '.join('%06X' % v for v in sorted(allow)))
        print('\n  Add one with --throttle-only ADDR. Anything already in the')
        print('  FFNx-named set is left at full rate on purpose -- its timing')
        print('  comes from scaled constants instead.')
        for va in sorted(EFFECT60_SLOTS):
            tag = ('full rate (FFNx-named)' if va in named else
                   'THROTTLED by aura-throttle' if va in allow else
                   'full rate -- FFNx would throttle this')
            print('    0x%06X  %s' % (va, tag))
        return 0

    if a.list_groups:
        print('confirmed on hardware, always applied:')
        print('  exe %d bytes, main %d words'
              % (len(EXE_CONFIRMED), len(NSO_CONFIRMED)))
        print('\nunverified groups (--enable NAME):')
        for g in sorted(set(NSO_GATED) | set(EXE_GATED)):
            n = len(NSO_GATED.get(g, [])) + len(EXE_GATED.get(g, []))
            if g in DISPATCH_GROUPS:
                d = DISPATCH_SITES.get(DISPATCH_GROUPS[g], {})
                print('  %-18s  2 code cave(s)  dispatcher first-frame scaler, '
                      '%s case(s)' % (g, d.get('cases') and len(d['cases'])))
                continue
            if g == CAMERA_WAIT_GROUP:
                print('  %-18s  %d code cave(s)  battle camera SCRIPT waits '
                      '(opcode 0xF5) x--batt-mult;' % (g, len(CAMERA_WAIT_SITES)))
                print('  %-18s                  this is what paces magic, '
                      'limit break and summon cameras' % '')
                continue
            if g == FIELD_BLINK_GROUP:
                print('  %-18s  %d code cave(s)  eye blink SHUT DURATION -- '
                      'holds mode 2 for a second frame;' % (g, len(FIELD_BLINK_SITES)))
                print('  %-18s                  pair with `field-blink`, '
                      'which sets the interval' % '')
                continue
            if g == FIELD_WAIT_GROUP:
                print('  %-18s  %d code cave(s)  FIELD script waits (opcode '
                      '0x24 WAIT) x--scaler-mult;' % (g, len(FIELD_WAIT_SITES)))
                print('  %-18s                  this is what paces every '
                      'scripted field scene -- flashing' % '')
                print('  %-18s                  lights, alarms, timed NPC '
                      'business and the SFX cued with them' % '')
                continue
            if g in THROTTLE_GROUPS:
                t = (DISPATCH_SITES.get(THROTTLE_GROUPS[g]) or {}).get(
                    'throttle') or {}
                print('  %-18s  3 code cave(s)  pause-throttle: every slot '
                      'function except %d named ones runs'
                      % (g, len(t.get('exclude', ()))))
                print('  %-18s                  1 frame in --batt-mult, via '
                      'g_is_battle_paused' % '')
                continue
            if g in SCALER_GROUPS:
                what = {s['name']: s['what'] for s in OPCODE_SITES}
                sites = SCALER_GROUPS[g]
                print('  %-18s %3d code cave(s) post-call return-value scaler: '
                      '%s' % (g, len(sites), ', '.join(sites)))
                if len(sites) == 1:
                    print('  %-18s                  %s'
                          % ('', what.get(sites[0], '')))
                continue
            if g == AUTORUN_GROUP:
                print('  %-18s   1 patch(es)   the stick no longer holds the '
                      'run button past 90%% deflection' % g)
                continue
            src = 'resolver' if g.startswith('r-') else 'hand-derived'
            print('  %-18s %3d patch(es)   %s' % (g, n, src))
        print('  %-18s 2 code cave(s)  right stick click ignored -- the '
              'port\'s HP/MP/limit booster' % NOCHEATS_GROUP)
        return 0

    # One folder instead of two paths. The dump already contains both inputs;
    # naming it is less error-prone than pointing at each and hoping they came
    # from the same rip. An explicit --exe/--nso still wins, so a patched or
    # substituted file can be dropped in without unpacking a whole dump.
    if a.dump:
        root = os.path.expanduser(a.dump)
        found = {}
        for key, rel in (('exe', os.path.join('romfs', 'ff7', 'resources',
                                              'ff7_1.02', 'ff7_en')),
                         ('nso', os.path.join('exefs', 'main'))):
            path = os.path.join(root, rel)
            if getattr(a, key):
                continue
            if not os.path.exists(path):
                raise SystemExit(
                    'ABORT  --dump %s has no %s\n       expected %s\n'
                    '       (pass --%s explicitly if it lives elsewhere)'
                    % (a.dump, rel, path, key))
            setattr(a, key, path)
            found[key] = path
        for key, path in found.items():
            print('--dump: %-3s %s' % (key, path))
    if not a.exe or not a.nso:
        raise SystemExit(
            'ABORT  need the game files: either --dump DIR, or both --exe and '
            '--nso')

    want = set()
    for item in a.enable:
        want |= {s.strip() for s in item.split(',') if s.strip()}
    known = set(NSO_GATED) | set(EXE_GATED)
    # Cave-only groups: they have no constant table, so they are not in
    # NSO_GATED/EXE_GATED, but they are still legal names to --enable.
    known |= {CAMERA_WAIT_GROUP, FIELD_WAIT_GROUP, FIELD_BLINK_GROUP,
              MOVIE_FPS_GROUP, MOVIE_POLL_GROUP, MOVIE_UPDATE_GROUP,
              ANALOG_GROUP, NOCHEATS_GROUP}
    if a.legacy:
        want |= {'legacy', 'swirl'}
    if a.enable_all:
        # 'legacy' duplicates other groups by construction. `p-` groups scale
        # only part of a function and desynchronise it. INCOMPLETE groups need
        # code changes we cannot make. None belong in a "turn everything on"
        # build; all remain selectable by name.
        #
        # This UNIONS rather than assigns. It used to assign, which silently
        # discarded every explicit --enable given alongside --enable-all: the
        # first six test builds of this session came out byte-identical to the
        # baseline and looked like a null result rather than a bug.
        # SCALER_GROUPS (opcode-scale, nfade) belong in this list and were
        # missing from it. They are CODE CAVES -- eight of them, all in the
        # field opcode handlers -- and every other cave-based group is
        # excluded here on exactly the grounds that a cave can crash rather
        # than merely misbehave. Because they are registered in NSO_GATED with
        # an empty patch list, `set(known)` picked them up and --enable-all
        # turned them on while the note printed below said the opposite. Every
        # "--legacy --enable-all" build ever tested therefore had eight
        # untested field code injections in it that the operator believed were
        # off. `nfade` was worse than untested: it is the divide half of the
        # field-fade subsystem whose other half, r-field_fade, is excluded as
        # INCOMPLETE -- precisely the half-applied state this file forbids
        # everywhere else.
        want |= (set(known) - {'legacy'} - PARTIAL - CODE_PAIRED
                 - set(INCOMPLETE) - set(DISPATCH_GROUPS)
                 - set(THROTTLE_GROUPS) - {CAMERA_WAIT_GROUP}
                 - {FIELD_WAIT_GROUP, FIELD_BLINK_GROUP}
                 # movie-fps injects code AND assumes the movie set on disk
                 # has been rebuilt at the doubled rate. Turning it on
                 # without that half halves the frame counter for 15 fps
                 # movies and desyncs them the other way, so it can only ever
                 # be enabled by the caller that does both -- never by
                 # "turn everything on".
                 - {MOVIE_FPS_GROUP, MOVIE_POLL_GROUP,
                    MOVIE_UPDATE_GROUP, ANALOG_GROUP}
                 # The two input tweaks CHANGE HOW THE GAME PLAYS rather than
                 # how fast it runs. Nobody asking for "every frame-rate fix"
                 # is asking to have the run button taken off their stick or
                 # a controller input disabled. Opt-in only, by name or by
                 # the checkbox.
                 - {AUTORUN_GROUP, NOCHEATS_GROUP}
                 - set(SCALER_GROUPS) - set(NEW_UNTESTED))
        # `--legacy --enable-all` is the confirmed baseline command, and under
        # the old assigning behaviour that combination dropped 'legacy' (its
        # patches are hand-typed duplicates of groups enable-all turns on
        # anyway). Keep dropping it, or the baseline stops reproducing.
        want.discard('legacy')
        skipped = sorted(((PARTIAL | CODE_PAIRED | set(INCOMPLETE)
                           | set(DISPATCH_GROUPS) | set(THROTTLE_GROUPS)
                           | {MOVIE_FPS_GROUP, MOVIE_POLL_GROUP,
                              MOVIE_UPDATE_GROUP, ANALOG_GROUP}
                           | {CAMERA_WAIT_GROUP, FIELD_WAIT_GROUP,
                              FIELD_BLINK_GROUP}
                           | set(SCALER_GROUPS) | set(NEW_UNTESTED))
                          & known) - want)
        print('note: --enable-all applies only fully-covered CONSTANT groups.')
        print('      excluded (%d): %s' % (len(skipped), ', '.join(skipped)))
        print('      p-* scale only part of a function; c-* sit in a function '
              'FFNx replaces outright.')
        print('      Every group that INJECTS CODE is excluded -- *-scale, '
              '*-throttle,')
        print('      opcode-scale, nfade, camera-wait, field-wait. Enable '
              'those one at a time.')
        print('      Enable any of them by name to experiment.')
        if QUARANTINED:
            print('      quarantined (not framerate patches, unreachable): %s'
                  % ', '.join('%s x%d' % (g, k) for g, k in QUARANTINED))
    drop = set()
    for item in a.disable:
        drop |= {s.strip() for s in item.split(',') if s.strip()}
    bad = (want | drop) - known
    if bad:
        raise SystemExit('ABORT  unknown group(s): %s\n       known: %s'
                         % (', '.join(sorted(bad)), ', '.join(sorted(known))))
    want -= drop


    exe_patches = list(EXE_CONFIRMED)
    if a.limiter_fps and a.limiter_fps != 60:
        exe_patches = retarget_limiters(exe_patches, a.limiter_fps)
    nso_patches = list(NSO_CONFIRMED)
    for g in sorted(want):
        exe_patches += EXE_GATED.get(g, [])
        nso_patches += NSO_GATED.get(g, [])
    drop_syms = set()
    for item in a.disable_sym:
        drop_syms |= {s.strip() for s in item.split(',') if s.strip()}
    if drop_syms:
        def keeps(p):
            return not any(p[0].startswith(s) for s in drop_syms)
        before = len(exe_patches) + len(nso_patches)
        exe_patches = [p for p in exe_patches if keeps(p)]
        nso_patches = [p for p in nso_patches if keeps(p)]
        gone = before - len(exe_patches) - len(nso_patches)
        if not gone:
            raise SystemExit('ABORT  --disable-sym %s matched nothing'
                             % ', '.join(sorted(drop_syms)))
        print('       dropped %d patch(es) for %s'
              % (gone, ', '.join(sorted(drop_syms))))

    dispatch_tags = [DISPATCH_GROUPS[g] for g in sorted(want)
                     if g in DISPATCH_GROUPS]
    throttle_tags = [THROTTLE_GROUPS[g] for g in sorted(want)
                     if g in THROTTLE_GROUPS]
    if len(set(throttle_tags)) != len(throttle_tags):
        clash = sorted(g for g in want if g in THROTTLE_GROUPS)
        raise SystemExit(
            'ABORT  %s all throttle the same dispatcher and hook the same\n'
            '       instructions -- enable exactly one of them.\n'
            '       effect60-throttle throttles all 60 slots (FFNx\'s rule, but\n'
            '       without its interpolation the battle visibly steps at 15 Hz);\n'
            '       aura-throttle throttles only the aura spawner and the magic\n'
            '       aura handler, which is what actually needs it.'
            % ', '.join(clash))
    allow_tags = {THROTTLE_GROUPS[g] for g in want
                  if g in THROTTLE_ALLOW_GROUPS}
    throttle_only = []
    for item in a.throttle_only:
        for s_ in item.split(','):
            s_ = s_.strip()
            if not s_:
                continue
            try:
                va = int(s_, 16)
            except ValueError:
                raise SystemExit('ABORT  --throttle-only %s is not a hex '
                                 'address' % s_)
            if va not in EFFECT60_SLOTS:
                raise SystemExit(
                    'ABORT  --throttle-only 0x%06X is not an effect60 slot '
                    'function.\n       Run --list-effect60 for the %d valid '
                    'addresses.' % (va, len(EFFECT60_SLOTS)))
            throttle_only.append(va)
    if throttle_only and not (allow_tags & set(throttle_tags)):
        raise SystemExit('ABORT  --throttle-only needs the aura-throttle group')
    throttle_exclude = set()
    for item in a.throttle_exclude:
        throttle_exclude |= {s.strip() for s in item.split(',') if s.strip()}
    if throttle_exclude and not throttle_tags:
        raise SystemExit('ABORT  --throttle-exclude given but no *-throttle '
                         'group is enabled')
    dispatch = (dispatch_caves(dispatch_tags, throttle_tags, a.batt_mult,
                               sorted(throttle_exclude), allow_tags,
                               throttle_only)
                if (dispatch_tags or throttle_tags) else [])
    if CAMERA_WAIT_GROUP in want:
        if a.camdat:
            raise SystemExit(
                'ABORT  --camdat and the %s group both scale the camera script '
                'waits.\n'
                '       Together they would be x%d. --camdat rewrites the F5\n'
                '       operand bytes in camdat0/1/2.bin, which clamps every '
                'wait above 0x3F at\n'
                '       255 and cannot touch the battle-intro scripts inside '
                'ff7_en; %s does\n'
                '       neither. Drop --camdat.'
                % (CAMERA_WAIT_GROUP, a.batt_mult * a.cam_mult,
                   CAMERA_WAIT_GROUP))
        dispatch += camera_wait_caves(True, a.batt_mult)
    if FIELD_WAIT_GROUP in want:
        dispatch += field_wait_caves(True, a.scaler_mult)
    if FIELD_BLINK_GROUP in want:
        if 'field-blink' not in want:
            print('note: %s widens the blink test and holds the reload, but '
                  'the INTERVAL constants are in `field-blink`.' % FIELD_BLINK_GROUP)
            print('      Without them the eyes will stay shut for the right '
                  'time and blink twice as often.')
        dispatch += field_blink_caves(True)
    if MOVIE_FPS_GROUP in want:
        dispatch += movie_frame_caves(True, a.movie_ratio)
    if ANALOG_GROUP in want:
        dispatch += analog_360_caves(True)
    if NOCHEATS_GROUP in want:
        dispatch += nocheats_caves(True)
    if MOVIE_POLL_GROUP in want and MOVIE_UPDATE_GROUP in want:
        raise SystemExit(
            'ABORT  %s and %s both correct the MVIEF poll count and must not '
            'be combined.\n'
            '       %s restores the vanilla field tick rate during a movie, '
            'which already\n'
            '       makes the poll count vanilla; halving it again would put '
            'every camera cue\n'
            '       twice as late as stock. Enable one or the other.'
            % (MOVIE_POLL_GROUP, MOVIE_UPDATE_GROUP, MOVIE_UPDATE_GROUP))
    if MOVIE_POLL_GROUP in want:
        dispatch += movie_poll_caves(True, a.scaler_mult)
    if MOVIE_UPDATE_GROUP in want:
        if MOVIE_FPS_GROUP not in want:
            print('note: %s consumes the extra decoded frames, but the frame '
                  'COUNTER still' % MOVIE_UPDATE_GROUP)
            print('      advances once per drawn frame. Without %s, '
                  'get_movie_frame keeps' % MOVIE_FPS_GROUP)
            print('      reporting the multiplied number and the opening '
                  'music cue stays early.')
        dispatch += movie_update_caves(True, a.movie_ratio)

    scaler_names = set()
    for g in sorted(want):
        scaler_names |= set(SCALER_GROUPS.get(g, ()))
    scalers = opcode_scaler_group(scaler_names)
    if scaler_names and not scalers:
        raise SystemExit('ABORT  %s requested but ff7nx_patchgroups.py has no '
                         'OPCODE_SITES -- regenerate it with ff7nx_resolve.py'
                         % ', '.join(sorted(scaler_names & set(SCALER_GROUPS))))

    print('build: confirmed base + %d group(s)%s'
          % (len(want), (': ' + ', '.join(sorted(want))) if want else
             '  (nothing unverified enabled)'))
    print('       %d exe byte-patches, %d main word-patches, %d code cave(s)'
          % (len(exe_patches), len(nso_patches), len(scalers) + len(dispatch)))
    if dispatch:
        print('       dispatcher scalers: %s'
              % ', '.join(sorted(set(c['tag'] for c in dispatch))))

    exe = open(a.exe, 'rb').read()
    nso = open(a.nso, 'rb').read()

    print()
    # WHICH exe gets identified.
    #
    # `identify_exe` hashes .text[0:0x3B4639] to prove the code is the build
    # every address in this file was derived from. That is the right check,
    # and it is also why a UI mod could take the whole 60 FPS set down with
    # it: Enhanced Stock UI's HEXT files write 1651 patches into that exact
    # range, so by the time the 60 FPS step ran, the hash no longer matched
    # and everything aborted.
    #
    # The check is about IDENTITY, not about the bytes we are handed. The
    # caller passes the stock exe from the dump for that, and patching goes
    # on targeting the HEXT-baked one. Nothing here writes into .text -- the
    # exe patches are all .rdata and .data -- so the two cannot collide.
    identify_exe(open(a.exe_identity, 'rb').read() if a.exe_identity else exe)
    if a.exe_identity:
        print('       (identity taken from the stock exe; the file being '
              'patched has HEXT baked in)')
    new_exe = patch_exe(exe, exe_patches, a.verify)
    print('main    %s  %d bytes' % (hashlib.md5(nso).hexdigest(), len(nso)))
    extra = battle_cam_patches(a.cam_step) if a.cam_step else ()
    new_nso = patch_nso(nso, nso_patches, a.verify, extra=extra,
                        throttle=a.cam_throttle, nframes=a.cam_nframes,
                        scalers=scalers, scaler_mult=a.scaler_mult,
                        dispatch=dispatch, batt_mult=a.batt_mult,
                        nso_path=a.nso)

    if a.verify:
        print('\nboth inputs are stock and all patches apply cleanly.')
        return 0

    if not selfcheck_nso(new_nso, nso_patches, extra=extra, dispatch=dispatch,
                        batt_mult=a.batt_mult):
        raise SystemExit('ABORT  rebuilt NSO failed self-check')
    print('\nrebuilt NSO self-check passed')

    # Independent audit: decompress both NSOs and diff .text word by word. The
    # self-check confirms the patches we asked for are present; this confirms
    # nothing ELSE changed. A structurally perfect NSO with one stray word is
    # exactly what corrupted an earlier build.
    _, old_raw = nso_segments(nso)
    _, new_raw = nso_segments(new_nso)
    n = min(len(old_raw[0]), len(new_raw[0]))
    diffs = [i for i in range(0, n, 4)
             if old_raw[0][i:i + 4] != new_raw[0][i:i + 4]]
    expect = {off for _l, off, _o, _n in list(nso_patches) + list(extra)}
    grown = len(new_raw[0]) - len(old_raw[0])
    stray = sorted(set(diffs) - expect)
    print('.text diff: %d word(s) changed, %d expected, %d byte(s) appended'
          % (len(diffs), len(expect), grown))
    if stray:
        hooked = ({c['hook'] for c in NSO_CAVES} | set(CAM_NFRAMES_SITES)
                  | {h for _l, h, _o in CAM_FNS}
                  | {s['hook'] for s in scalers}
                  | {c['site']['hook'] for c in dispatch}
                  | {WALK_TICK_HOOK, WALK_X_HOOK, WALK_Y_HOOK,
                     WALK_PFLAG_SET_HOOK, WALK_PFLAG_CLR_HOOK,
                     BATTLE_MOVE_X_HOOK, BATTLE_MOVE_Z_HOOK,
                     BATTLE_MOVE_Y_HOOK, BATTLE_MOVE_YIDX_HOOK})
        # Caves chained through reclaimed padding change words all over .text
        # rather than appending to the tail, so each one is listed explicitly
        # -- the audit still accounts for every changed word, it just has more
        # of them to account for.
        padwords = set()
        for c in dispatch:
            padwords |= set(c.get('placed') or ())
        unexplained = [o for o in stray if o not in hooked and o not in padwords]
        for o in stray[:20]:
            print('    +0x%06X %s'
                  % (o, 'cave hook' if o in hooked else
                     'padding cave' if o in padwords else 'UNEXPLAINED'))
        if unexplained:
            raise SystemExit('ABORT  %d unexplained .text change(s) -- refusing '
                             'to ship a build we cannot account for'
                             % len(unexplained))
    missing = sorted(expect - set(diffs))
    if missing:
        raise SystemExit('ABORT  %d patch(es) did not change anything: %s'
                         % (len(missing), ['0x%X' % m for m in missing]))

    root = os.path.join(a.out, 'atmosphere', 'contents', a.title_id)
    p_exe = os.path.join(root, 'romfs', 'ff7', 'resources', 'ff7_1.02', 'ff7_en')
    p_nso = os.path.join(root, 'exefs', 'main')
    for p, blob in ((p_exe, new_exe), (p_nso, new_nso)):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'wb').write(blob)
        print('wrote %s  (%s)' % (p, hashlib.md5(blob).hexdigest()))

    if a.camdat:
        if a.cam_mult <= 1:
            print('\ncamdat: --cam-mult %d, nothing to do' % a.cam_mult)
        else:
            # Same romfs layout the 7th Heaven NX build uses for the
            # archives: romfs/ff7/workingdir/data/... -- NOT romfs/ff7/...
            out_dir = os.path.join(root, 'romfs', 'ff7', 'workingdir',
                                   'data', 'lang-en', 'battle')
            os.makedirs(out_dir, exist_ok=True)
            print()
            for name in CAMDAT_FILES:
                src = os.path.join(a.camdat, name)
                if not os.path.isfile(src):
                    print('  !! %s not found in %s' % (name, a.camdat))
                    continue
                blob, found, changed = patch_camdat(open(src, 'rb').read(),
                                                    a.cam_mult)
                dest = os.path.join(out_dir, name)
                open(dest, 'wb').write(blob)
                print('  ok  %-12s %d camera wait(s), %d scaled x%d  (%s)'
                      % (name, found, changed, a.cam_mult,
                         hashlib.md5(blob).hexdigest()))

    if a.battle_lgp:
        if a.anim_mult <= 1:
            print('\nbattle.lgp: --anim-mult %d, nothing to do' % a.anim_mult)
        else:
            print()
            raw = open(a.battle_lgp, 'rb').read()
            patched, n, frac, clamped = battle_lgp_looks_patched(raw,
                                                                a.anim_mult)
            if patched and not a.force_lgp:
                raise SystemExit(
                    'ABORT  %s looks like it has ALREADY been scaled.\n'
                    '       %d of %d wait operands (%.0f%%) are divisible by '
                    '%d, and %d are at the 255 clamp.\n'
                    '       A stock archive sits near 25%%. Scaling is not '
                    'idempotent -- running it again\n'
                    '       would make it x%d and battle animations would '
                    'crawl, with nothing else to\n'
                    '       trace it by. Point --battle-lgp at the CLEAN 7th '
                    'Heaven NX output, or pass\n'
                    '       --force-lgp if you are certain this file is stock.'
                    % (a.battle_lgp, int(frac * n), n, frac * 100,
                       a.anim_mult, clamped, a.anim_mult ** 2))
            old_scaled, n_old, f_old, n_new, f_new = \
                battle_lgp_scaled_by_old_walker(raw, a.anim_mult)
            if old_scaled and not a.force_lgp:
                raise SystemExit(
                    'ABORT  %s was scaled by the PREVIOUS, broken ?ab walker.\n'
                    '       %d of the wait operands it could reach are %.0f%% '
                    'divisible by %d, while the\n'
                    '       %d it could NOT reach are only %.0f%%. Scaling '
                    'again would take that first\n'
                    '       group to x%d while the rest finally reach x%d -- '
                    'worse than either.\n'
                    '       Rebuild sdout/ with 7th_heaven_nx.py so this is a '
                    'clean archive, or pass\n'
                    '       --force-lgp if you are certain it is stock.'
                    % (a.battle_lgp, n_old, f_old * 100, a.anim_mult,
                       n_new, f_new * 100, a.anim_mult ** 2, a.anim_mult))
            print('      source looks stock (%d wait operands, %.0f%% '
                  'divisible by %d)' % (n, frac * 100, a.anim_mult))
            out = patch_battle_lgp(raw, a.anim_mult)
            dest = os.path.join(root, 'romfs', 'ff7', 'workingdir', 'data',
                                'battle', 'battle.lgp')
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            open(dest, 'wb').write(out)
            print('      wrote %s (%d bytes, size unchanged: %s)'
                  % (dest, len(out), len(out) == len(raw)))

    print('\ncopy the contents of %s to your SD card root.' % a.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())