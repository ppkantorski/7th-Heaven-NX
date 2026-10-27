#!/usr/bin/env python3
"""
ff7nx_30fps.py -- the Full 30 FPS rate for ff7nx_60fps.

The 60/30 FPS Gameplay mod has three FPS Modes. Full 60 FPS is what
ff7nx_60fps has always built. Full 30 FPS is FFNx's 30 FPS mode
(`ff7_fps_limiter = FPS_LIMITER_30FPS`): battles run at 30 instead of their
stock 15, so `battle_frame_multiplier` is 2 instead of 4, and the field and
the world map stay at their stock 30, so `common_frame_multiplier` is 1 and
every field and world fix is left out. The mod ships its battle animations
at 4x in both modes and nothing for the field in this one, which is exactly
what FFNx expects.

This module holds only what differs. `rate_tables()` takes the 60 FPS tables
ff7nx_60fps builds and returns the 30 FPS ones:

  * groups FFNx applies only to a 60 FPS field, world or swirl are dropped;
  * groups FFNx scales by `battle_frame_multiplier` get their x2 word, from
    the tables below, at exactly the offsets the x4 group patches -- a group
    whose x4 table names an offset with no x2 word aborts the build rather
    than shipping a x4 constant in a x2 battle;
  * groups FFNx writes the same way at any rate are kept as they are;
  * one patch FFNx makes only at 30, the battle model frame step, is added.

The x4 tables stay the single source of every offset and stock word. Each x2
word here is checked against the x4 table's stock word before it is used.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import struct

RATES = ('60', '30', 'stock')


def _f(x):
    return struct.pack('<f', x)


# --------------------------------------------------------------------------
# Groups that FFNx applies only when the field and world run at 60. At 30
# they are left at stock: the field and the world map are not touched.
# --------------------------------------------------------------------------
FIELD_60_ONLY = frozenset((
    'field-blink', 'field-blink-hold', 'field-text', 'field-wait',
    'opcode-scale', 'opcode-scale-safe', 'nfade', 'scale-jump',
    'scale-scrlc', 'scale-scrla', 'scale-scr2dc', 'scale-scr2dl',
    'scale-scrlp', 'scale-ofst', 'scale-vwoft',
    'r-field_models', 'r-field_text', 'r-world', 'r-swirl', 'swirl',
    # movie-poll halves the MVIEF poll count, which only a 60 FPS field
    # doubles. movie-fps (the decoded-frame divider) is rate-independent.
    'movie-poll', 'movie-update',
))

# Groups FFNx writes identically at any battle rate. FFNx patches the Tifa
# reel, the Phoenix camera and Odin/Shiva/Bahamut bytes with literals, not
# with battle_frame_multiplier; the input tweaks are not frame-rate patches.
SAME_AT_ANY_RATE = frozenset((
    'tifa-slots', 'phoenix-camera', 'r-summons',
    'analog-360', 'no-autorun', 'no-cheats', 'movie-fps',
))

# The battle dispatcher, camera-wait and battle-move caves take the battle
# multiplier as an argument and are built at x2 by the generator.
BUILT_FOR_RATE = frozenset((
    'effect10-scale', 'effect100-scale', 'effect60-scale', 'camera-scale',
    'effect100-throttle', 'effect60-throttle', 'aura-throttle',
    'camera-wait',
))

# --------------------------------------------------------------------------
# ff7_en
# --------------------------------------------------------------------------
EXE_CONFIRMED_30 = [
    ('battle limiter divisor 15.0 -> 30.0', 0x7C0B00,
     struct.pack('<d', 15.0), struct.pack('<d', 30.0)),
]

# FFNx animations.cpp y_pos_offset_display_damage_30, verbatim.
DAMAGE_TABLE_30 = bytes([0, 1, 2, 3, 4, 5, 6, 6, 7, 7, 8, 8, 8, 8, 7, 7, 6, 6,
                         5, 4, 3, 2])
assert len(DAMAGE_TABLE_30) == 22

# group -> {va: (label, new bytes)}; the stock bytes come from the x4 table.
# The damage table goes where the x4 one does; it is shorter, so its stock
# bytes are the first 22 of the x4 entry's zeros.
EXE_X2 = {
    'r-enemy_death': {
        0x7B7680: ('disintegrate-2 death rate 22.5 -> 11.25', _f(11.25)),
    },
    'damage-numbers': {
        0x7B9D80: ('30fps damage y-offset table (22 bytes into .rdata '
                   'padding)', DAMAGE_TABLE_30),
    },
}

# --------------------------------------------------------------------------
# exefs/main -- x2 words for the hand-derived groups, at the offsets of their
# x4 entries. Each is the x4 edit with the multiplier halved; the comments
# give the stock, x4 and x2 meaning.
# --------------------------------------------------------------------------
NSO_X2 = {
    'victory': {
        0x0009D668: ('battle_sub_430DD0+0x326 outro wait 30 -> 60',
                     0x52800788),                    # mov w8, #60
        0x0009E550: ('battle_sub_430DD0+0x361 outro wait 8 -> 16',
                     0x52800208),                    # mov w8, #16
        0x0009E510: ('battle_sub_430DD0+0x3DE outro wait 49 -> 98',
                     0x52800C48),                    # mov w8, #98
    },
    'victory-fade': {
        0x0009E348: ('battle_sub_430DD0+0x60E results-screen gate 16 -> 32 '
                     'frames (the K-1 half)', 0x528003EB),   # mov w11, #31
        0x0009E350: ('battle_sub_430DD0+0x60E results-screen gate 16 -> 32 '
                     'frames (the K half)', 0x51008109),     # sub w9, w8, #0x20
    },
    'damage-numbers': {
        0x007C5400: ('display_battle_damage_5BB410+0x54 hold 11 -> 22 frames',
                     0x528002C8),                    # mov w8, #22
        # The table pointer is the same address at either rate.
        0x007C51B4: (None, 0x5293B018),
        0x007C51B8: (None, 0x72A00F78),
    },
    'limit-aura': {
        0x007D81E4: ('limit_break_aura_effects_5C0572+0x4C aura growth '
                     '0x600 -> 0x300', 0x53185D18),  # lsl w24, w8, #8
        0x007D8244: ('limit_break_aura_effects_5C0572+0x6E phase 1 boundary '
                     '8 -> 16', 0x7100413F),         # cmp w9, #16
        0x007D8300: ('limit_break_aura_effects_5C0572+0x7A aura growth '
                     '0xC00 -> 0x600', 0x53175918),  # lsl w24, w8, #9
        0x007D8284: ('limit_break_aura_effects_5C0572+0x98 phase 2 boundary '
                     '16 -> 32', 0x7100811F),        # cmp w8, #32
        0x007D82D8: ('limit_break_aura_effects_5C0572+0xAD+0xB0 (x-8)<<9 -> '
                     '(x-16)<<8', 0x53185D08),       # lsl w8, w8, #8
        0x007D853C: ('limit_break_aura_effects_5C0572+0x13E aura ends at '
                     'frame 15 -> 30', 0x7100791F),  # cmp w8, #30
    },
    'aura-eskill': {
        0x007D86EC: ('enemy_skill_aura_effects_5C06BF+0x5C,+0x64 (14-c)<<7 '
                     '-> (28-c)<<6', 0x4B091914),    # sub w20, w8, w9, lsl #6
        0x007D8764: ('enemy_skill_aura_effects_5C06BF+0x81,+0x89 (14-c)<<10 '
                     '-> (28-c)<<9', 0x4B082535),    # sub w21, w9, w8, lsl #9
        0x007D87D0: ('enemy_skill_aura_effects_5C06BF+0xA7 phase boundary '
                     '8 -> 16', 0x7100413F),         # cmp w9, #16
        0x007D8808: ('enemy_skill_aura_effects_5C06BF+0xD6,+0xD9 (c-8)<<12 '
                     '-> (c-16)<<11', 0x53155108),   # lsl w8, w8, #11
        0x007D8834: ('enemy_skill_aura_effects_5C06BF+0xB3 c * -4096 -> '
                     'c * -2048', 0x4B082FE8),       # neg w8, w8, lsl #11
        0x007D8AA8: ('enemy_skill_aura_effects_5C06BF+0x182 aura ends at '
                     'frame 15 -> 30', 0x7100791F),  # cmp w8, #30
    },
    'aura-summon': {
        0x007D9144: ('summon_aura_effects_5C0953+0x4D (c<<12)/8 -> '
                     '(c<<11)/8', 0x53185D08),       # lsl w8, w8, #8
        0x007D9704: ('summon_aura_effects_5C0953+0x19D phase boundary 8 -> 16',
                     0x7100411F),                    # cmp w8, #16
    },
    'boss-death': {
        0x007C9130: ('battle_boss_death_5BC48C+0x40,+0xCF n_frames 64 -> 128',
                     0x52801016),                    # mov w22, #128
        0x007C9CD0: ('battle_boss_death_sub_5BC5EC flash trigger 58 -> 116',
                     0x7101D11F),                    # cmp w8, #116
        0x007C9D08: ('battle_boss_death_sub_5BC5EC flash trigger 64 -> 128',
                     0x7102011F),                    # cmp w8, #128
        0x007C9DB0: ('battle_boss_death_sub_5BC5EC wobble toggles on bit 1, '
                     'not bit 0', 0x53010668),       # ubfx w8, w19, #1, #1
        0x007C9AAC: ('battle_boss_death_sub_5BC6ED+0xCF per-frame step '
                     '0x20 -> 0x10', 0x11004108),    # add w8, w8, #0x10
        0x007C9B04: ('battle_boss_death_sub_5BC6ED+0xF1 per-frame step 4 -> 2',
                     0x51000908),                    # sub w8, w8, #2
    },
    'battle-text': {
        0x007CF280: ('get_n_frames_display_action_string (byte >> 2) -> '
                     '(byte >> 1)', 0x13017D08),     # asr w8, w8, #1
        0x007CF284: ('get_n_frames_display_action_string + 4 -> + 8',
                     0x11002108),                    # add w8, w8, #8
        0x007CF188: ('battle dialogue, the inlined copy: (byte >> 2) -> '
                     '(byte >> 1)', 0x13017D08),     # asr w8, w8, #1
        0x007CF18C: ('battle dialogue, the inlined copy: + 4 -> + 8',
                     0x11002108),                    # add w8, w8, #8
    },
    'summon-runtime': {
        0x007DF46C: ('summon setup duration 45 -> 90', 0x52800B48),
        # Shiva's 92/91 and the two camera NOPs are FFNx literals.
        0x00700500: (None, 0x51017109),
        0x00700534: (None, 0x71016D1F),
        0x006EF7B8: (None, 0xD503201F),
        0x002C7C48: (None, 0xD503201F),
    },
    'script-wait': {
        0x007DF1FC: ('g_script_wait_frames 14 -> 28 (1 of 4)', 0x52800393),
        0x007E1054: ('g_script_wait_frames 14 -> 28 (2 of 4)', 0x52800393),
        0x007E1D40: ('g_script_wait_frames 14 -> 28 (3 of 4)', 0x52800393),
        0x00833800: ('g_script_wait_frames 14 -> 28 (4 of 4)', 0x52800388),
    },
    'fps-menu': {
        0x00090AE8: ('battle FPS menu multiplier 4 -> 2', 0x52800048),
    },
}

# Resolver output (ff7nx_resolve.py --battle-mult 2 --common-mult 1) for the
# battle groups the 60 FPS preset ships, at their x4 offsets. Labels are
# FFNx's own patch sites. battle_menu_closing_window_box_6DAEF0 is absent on
# purpose: FFNx scales it only at 60.
RESOLVED_X2 = {
    'r-battle_aura': [
        ('tifa_limit_2_1_sub_4E48D4+0x1FE', 0x003F5ADC, 0x7100311F, 0x7100611F),
        ('vincent_limit_fade_effect_sub_5D4240+0x24', 0x00833708, 0x321F0FE8, 0x52800788),
        ('vincent_limit_fade_effect_sub_5D4240+0x57', 0x008337C0, 0x321C03E8, 0x52800408),
        ('vincent_limit_fade_effect_sub_5D4240+0x6E', 0x00833800, 0x321F0BE8, 0x52800388),
    ],
    'r-battle_camera': [
        ('battle_sub_429AC0+0x152', 0x000D1F90, 0x528007A8, 0x52800F48),
        ('battle_sub_429D8A+0x1D8', 0x000D5DCC, 0x321F0FE8, 0x52800788),
    ],
    'r-battle_damage': [
        ('battle_handle_player_mark_5B9C8E+0x6A', 0x007BE21C, 0x11020108, 0x11010108),
    ],
    'r-battle_misc': [
        ('battle_sub_425E5F+0x3A', 0x000C0F00, 0x320007E8, 0x528000C8),
        ('battle_sub_42A72D+0x11A', 0x000D3994, 0x321F0BE8, 0x52800388),
        ('battle_sub_434C8B+0x4F', 0x000F34FC, 0x32000FE8, 0x528003C8),
        ('battle_sub_435D81+0x6A8', 0x000F6C0C, 0x528005E8, 0x52800BC8),
        ('battle_sub_5BCD42+0x5B', 0x007CB3C0, 0x32000FE8, 0x528003C8),
        ('battle_sub_5BCD42+0x6E', 0x007CB3E8, 0x321C03E8, 0x52800108),
        ('battle_sub_5BCF9D+0x3A', 0x007CBA94, 0x528002A8, 0x52800548),
        ('battle_sub_5BD050+0x1DC', 0x007CC35C, 0x531E7508, 0x531F7908),
        ('battle_sub_5BD050+0x203', 0x007CC3CC, 0x531E7508, 0x531F7908),
    ],
    'r-battleground': [
        ('battleground_midgar_flashback_rain_5BDC4F+0x44', 0x007CE02C, 0x7100211F, 0x7100411F),
        ('battleground_midgar_flashback_rain_5BDC4F+0x156', 0x007CE3F0, 0x11008108, 0x11004108),
        ('battleground_midgar_flashback_rain_5BDC4F+0x71', 0x007CE4C0, 0x11064108, 0x11032108),
    ],
    'r-enemy_death': [
        ('battle_disintegrate_2_death_5BBA82+0x40', 0x007C6CEC, 0x321F0BE8, 0x52800388),
        ('battle_disintegrate_2_death_sub_5BBBDE+0xB9', 0x007C7288, 0x11020108, 0x11010108),
        ('battle_disintegrate_2_death_sub_5BBBDE+0xDB', 0x007C72E0, 0x51004108, 0x51002108),
        ('battle_enemy_death_5BBD24+0x40', 0x007C771C, 0x321F0BE8, 0x52800388),
        ('battle_enemy_death_sub_5BBE32+0xA8', 0x007C7AB4, 0x11020108, 0x11010108),
        ('battle_enemy_death_sub_5BBE32+0xCB', 0x007C7B0C, 0x51004108, 0x51002108),
        ('battle_melting_death_5BC21F+0x40', 0x007C88FC, 0x321F0BE8, 0x52800388),
        ('battle_melting_death_sub_5BC32D+0xAB', 0x007C8C9C, 0x11020108, 0x11010108),
        ('battle_melting_death_sub_5BC32D+0xCE', 0x007C8CF4, 0x51004108, 0x51002108),
        ('battle_melting_death_sub_5BC32D+0xEE', 0x007C8D50, 0x11040108, 0x11020108),
        ('battle_melting_death_sub_5BC32D+0x10D', 0x007C8DA8, 0x51040108, 0x51020108),
        ('battle_melting_death_sub_5BC32D+0x12C', 0x007C8E00, 0x11040108, 0x11020108),
        ('battle_morph_death_5BC812+0x40', 0x007CA110, 0x321C03E8, 0x52800408),
        ('battle_morph_death_sub_5BC920+0x9F', 0x007CA4C0, 0x11020108, 0x11010108),
        ('battle_morph_death_sub_5BC920+0xC2', 0x007CA518, 0x51002108, 0x51001108),
        ('battle_morph_death_sub_5BC920+0xE3', 0x007CA570, 0x51002108, 0x51001108),
        ('battle_morph_death_sub_5BC920+0x104', 0x007CA5C8, 0x51002108, 0x51001108),
        ('battle_morph_death_sub_5BC920+0x154', 0x007CA6A0, 0x11040108, 0x11020108),
        ('battle_iainuki_death_5BCAAA+0x40', 0x007CA990, 0x321C03E8, 0x52800408),
        ('battle_iainuki_death_sub_5BCBB8+0x9F', 0x007CAD40, 0x11020108, 0x11010108),
        ('battle_iainuki_death_sub_5BCBB8+0xC2', 0x007CAD98, 0x51002108, 0x51001108),
        ('battle_iainuki_death_sub_5BCBB8+0xE3', 0x007CADF0, 0x51002108, 0x51001108),
        ('battle_iainuki_death_sub_5BCBB8+0x104', 0x007CAE48, 0x51002108, 0x51001108),
        ('battle_iainuki_death_sub_5BCBB8+0x154', 0x007CAF20, 0x51020108, 0x51010108),
    ],
    'r-summons': [
        ('run_bahamut_neo_movement_48D7BC+0x62', 0x00271118, 0x51033D09, 0x51033909),
        ('run_bahamut_movement_49ADEC+0x69', 0x002AAAA8, 0x51015109, 0x51014D09),
        ('run_odin_gunge_movement_4A584D+0x1D0', 0x002DAF88, 0x7100611F, 0x7100691F),
    ],
}

# FFNx scales battle_menu_closing_window_box_6DAEF0 only at 60.
FFNX_60_ONLY_X2 = {
    'r-battle_misc': frozenset((0x00D6B7AC, 0x00D6B884)),
}

# FFNx animations.cpp: battle_sub_5BCF9D+0x3A is scaled at 60, and at 30 only
# when its widescreen mode is on.
WIDESCREEN_ONLY_X2 = {
    'r-battle_misc': frozenset((0x007CBA94,)),
}

# --------------------------------------------------------------------------
# The one patch FFNx makes only at 30 FPS:
#
#   if (ff7_fps_limiter == FPS_LIMITER_30FPS) {
#       patch_multiply_code<byte>(battle_update_3d_model_data + 0x13E, mult);
#       patch_multiply_code<byte>(battle_update_3d_model_data + 0x316, mult);
#   }
#
# battle_update_3d_model_data is x86 0x424B4B (the call at run_animation_
# script+0x623). Its two `add [frame], 1` advance a battle model one
# animation frame per call. The mod's battle animations are 4x long in both
# modes, which a 60 FPS battle plays one frame per frame; a 30 FPS battle
# has to step two. Both x86 sites clamp with `jl` to n-1 afterwards, so a
# step of 2 cannot run past the last frame.
#
# In the translation (body +0xB7FD0..+0xB8BF0) the two are the only
# `add w8, w8, #1` in the function.
# --------------------------------------------------------------------------
MODEL_STEP_GROUP = 'model-frame-step'
MODEL_STEP = [
    ('battle_update_3d_model_data+0x13E animation frame step 1 -> 2',
     0x000B83DC, 0x11000508, 0x11000908),
    ('battle_update_3d_model_data+0x316 animation frame step 1 -> 2',
     0x000B8A88, 0x11000508, 0x11000908),
]


def _apply_x2(group, patches, table, keep=frozenset()):
    """The x4 group's patches with their x2 words, or SystemExit."""
    out = []
    used = set()
    for p in patches:
        label, off, old, _new = p
        if off not in table:
            raise SystemExit('ABORT  Full 30 FPS: %s patches +0x%X (%s), which '
                             'has no 30 FPS value' % (group, off, label))
        lab30, new30 = table[off]
        used.add(off)
        if new30 is None:
            continue
        out.append((lab30 or label, off, old, new30))
    unused = set(table) - used - set(keep)
    if unused:
        raise SystemExit('ABORT  Full 30 FPS: %s no longer patches %s -- the '
                         'x2 table is out of date'
                         % (group, ', '.join('+0x%X' % o for o in sorted(unused))))
    return out


def _resolved_table(group, widescreen):
    rows = RESOLVED_X2[group]
    skip = set() if widescreen else set(WIDESCREEN_ONLY_X2.get(group, ()))
    table = {}
    for label, off, old, new in rows:
        table[off] = (label, None if off in skip else new)
    for off in FFNX_60_ONLY_X2.get(group, ()):
        table[off] = (None, None)
    return table, {off: old for _l, off, old, _n in rows}


def rate_tables(rate, want, exe_gated, nso_gated, widescreen=False,
                log=print):
    """
    (want, exe_confirmed, nso_confirmed, exe_patches, nso_patches) for `rate`.

    `want` is the set of groups the 60 FPS command line selected. The tables
    passed in are ff7nx_60fps's and are not modified.
    """
    if rate not in RATES or rate == '60':
        raise SystemExit('ABORT  rate_tables() is for 30 and stock, not %r'
                         % rate)
    want = set(want)
    if rate == 'stock':
        # Original FPS: only what is not a frame-rate patch. Nothing in
        # ff7_en, nothing in battle.lgp.
        keep = want & {'analog-360', 'no-autorun', 'no-cheats', 'movie-fps'}
        dropped = sorted(want - keep)
        if dropped:
            log('rate stock: frame-rate groups left out: %s'
                % ', '.join(dropped))
        nso = []
        for g in sorted(keep):
            nso += nso_gated.get(g, [])
        return keep, [], [], [], nso

    dropped = sorted(want & FIELD_60_ONLY)
    want -= FIELD_60_ONLY
    exe, nso = [], []
    for g in sorted(want):
        e4 = exe_gated.get(g, [])
        n4 = nso_gated.get(g, [])
        if g in SAME_AT_ANY_RATE:
            exe += e4
            nso += n4
            continue
        if g in BUILT_FOR_RATE:
            if e4 or n4:
                raise SystemExit('ABORT  Full 30 FPS: %s has constant patches '
                                 'as well as caves' % g)
            continue
        if not e4 and not n4:
            raise SystemExit('ABORT  Full 30 FPS: no 30 FPS treatment is '
                             'known for %s' % g)
        if e4:
            if g not in EXE_X2:
                raise SystemExit('ABORT  Full 30 FPS: %s has ff7_en patches '
                                 'and no 30 FPS values' % g)
            for label, va, old, _new in e4:
                if va not in EXE_X2[g]:
                    raise SystemExit('ABORT  Full 30 FPS: %s patches ff7_en '
                                     '0x%X, which has no 30 FPS value' % (g, va))
                lab30, new30 = EXE_X2[g][va]
                exe.append((lab30, va, old[:len(new30)], new30))
        if n4:
            if g in RESOLVED_X2:
                table, stock = _resolved_table(g, widescreen)
                for _l, off, old, _n in n4:
                    if off in stock and stock[off] != old:
                        raise SystemExit(
                            'ABORT  Full 30 FPS: %s +0x%X stock word %08X in '
                            'the x4 table, %08X in the x2 one'
                            % (g, off, old, stock[off]))
                nso += _apply_x2(g, n4, table, keep=set(
                    off for off in table if off not in {p[1] for p in n4}))
            elif g in NSO_X2:
                nso += _apply_x2(g, n4, NSO_X2[g])
            else:
                raise SystemExit('ABORT  Full 30 FPS: %s has no 30 FPS values; '
                                 'it cannot be part of a 30 FPS build' % g)
    want.add(MODEL_STEP_GROUP)
    nso += MODEL_STEP
    if dropped:
        log('rate 30: field and world stay stock -- left out: %s'
            % ', '.join(dropped))
    return want, list(EXE_CONFIRMED_30), [], exe, nso
