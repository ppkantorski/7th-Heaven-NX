#!/usr/bin/env python3
"""
ff7nx_nocheats.py -- two small input tweaks.

DISABLE AUTO-RUN
================
The port turns a deep stick push into a held run button. In its DirectInput
`GetDeviceState` emulation (0x10D3820), after filling the key buffer from the
buttons it does:

    len = sqrt(up^2 + down^2 + right^2 + left^2)      axes 0x10..0x13
    if (len > 0.9f)                                   0x11AE6FC
        KEYBUF[0x52] = 0x80                           0x10D3B4C

0x52 is the OK/Cancel scancode the button loop also writes for A/B (ids 12 and
13, swapped by the confirm/cancel setting), so holding it is exactly what
holding the run button does. Turning the store into a `nop` removes the
SYNTHESISED press and nothing else: the real button still writes the same byte
from the same buffer a few instructions earlier, so running by holding the
button is untouched. One word, no cave.

The direction keys come from a separate, lower threshold -- `IsHeld` compares
each of those four floats against 0.4f (0x11AE7B8) -- so walking still works
at any tilt. Only the walk/run distinction goes.

NO CHEATS (right stick click)
=============================
The port's boosters are triggered by clicking the sticks: R3 fills HP/MP and
the limit gauge, L3 is 3x speed, L3+R3 turns random encounters off. Their
on-screen icons are in this module's own .rodata (`cheat_battleboost`,
`cheat_speedx3`, `cheat_speedx3_off`, `cheat_nobattle`, `cheat_skipdialog` at
0x11AAB32..).

All three are one function, 0x1880, run once a frame. It asks the input
object (vtable at 0x12CBA60) through two accessors, +0x38 `held(id)` and +0x48
`released(id)` (prev & ~cur), with id 0 = L3 and id 1 = R3, and keeps a latch
byte at 0x12CF0F0 so one gesture fires once:

    latch set:  latch = held(L3) || held(R3)          (clears when both up)
    else, first match wins, each then sets the latch:
      released(R3) && held(L3)       -> toggle no-battle  [0x12E60B8]
      released(L3) && held(R3)       -> toggle no-battle
      released(L3) && released(R3)   -> toggle no-battle
      released(L3) && !held(R3)      -> speed 1 <-> 3     [0x12C99B8]  0x1AA0
      released(R3) && !held(L3)      -> toggle booster    [0x12E60BA]  0x1A88

Everything fires on RELEASE, so the stock code already keeps the combo apart
from 3x speed: pressing L3 first and R3 a moment later never toggles speed,
because by the time L3 comes up R3 is held and the combo row matches first.

BUILD 736: so this group no longer hides R3. It patches the one row it is
about instead -- 0x1A88, the first word of the R3-alone toggle, becomes a
branch to 0x1ABC, which sets the latch and returns. R3 on its own now does
nothing, L3 is still 3x speed, and L3+R3 toggles random encounters again.
(0x1A90, the shared `ldrb/eor/strb`, is NOT touched: the no-battle rows reach
it through 0x19DC.) Nothing else in the module writes the booster byte.

The poll cave stays, minus its `and`: it still copies StickR to bit 62, but
leaves bit 5 where it was, so the booster function sees R3 again. Before
build 736 the cave cleared bit 5, and that is the only reason L3+R3 stopped
working. (BUILD 803: ff7nx_worldfar's world map view toggle + camera reset
moved from R3 to ZL+ZR, so nothing reads bit 62 any more; the cave is kept
as it is so the module's bytes do not change.)

Where the buttons come from is not in doubt:

  * `main` calls `nn::hid::GetNpadState` in exactly ONE place -- 0x111C028
    (FullKey) and 0x111C0C0 (Handheld), both inside the poll at 0x111BFC0.
  * That poll is the only writer of the 64-bit button mask, into `obj+0x20`
    (and the previous frame's copy at `obj+0x28`).

`0x11DDAE4` is the id -> nn::hid bit table the accessors use, and it maps id 0
to bit 4 (StickL) and id 1 to bit 5 (StickR). The DirectInput emulation's key
loop runs ids **2..0x13** -- it deliberately skips both stick clicks -- so
letting R3 through again gives the game's own key mapping nothing new.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import a64 as A

# --- disable auto-run: one word ------------------------------------------
AUTORUN_SITE = 0x10D3B4C
AUTORUN_ORIG = 0x390BF909            # strb w9, [x8, #0x2fe]   KEYBUF[0x52]
NOP = 0xD503201F

# --- no cheats: the poll's two button stores ------------------------------
# Both paths of the poll end with the same store; the FullKey one and the
# Handheld one. Both are hooked, because either can be the live path
# depending on how the console is being held.
BUTTON_STORES = (0x111C04C, 0x111C0D4)
BUTTON_STORE_ORIG = 0xF900126D       # str x13, [x19, #0x20]
STICKR_BIT = 5

# --- no cheats: the booster function's R3-alone row (BUILD 736) -----------
BOOSTER_FN = 0x1880
BOOSTER_R3_SITE = 0x1A88
BOOSTER_R3_ORIG = 0xB0009668         # adrp x8, 0x12ce000  (GOT -> booster byte)
BOOSTER_LATCH_RET = 0x1ABC           # mov w8, #1; strb w8, [latch]; ret
B_LATCH_RET = 0x1400000D             # b 0x1abc


def autorun_patch():
    """The word patch that removes the synthesised run key."""
    return [('auto-run: stick magnitude no longer holds the run button '
             '(KEYBUF[0x52])', AUTORUN_SITE, AUTORUN_ORIG, NOP)]


def booster_patch():
    """The word patch that takes R3-alone out of the booster function."""
    return [('no cheats: R3 on its own no longer toggles the HP/MP/limit '
             'booster (L3 = 3x speed, L3+R3 = no encounters stay)',
             BOOSTER_R3_SITE, BOOSTER_R3_ORIG, B_LATCH_RET)]


# BUILD 590: the world map's camera reset (R3, FFNx's) still needs to see
# the click, at bit 62, which is no nn::hid button, so nothing but
# ff7nx_worldfar's native code, which asks for it by name, reads it.
# BUILD 736: bit 5 is left in place as well (the `and` that cleared it is
# gone), so the booster function sees R3 for L3+R3; see booster_patch().
R3_SHADOW_BIT = 62
ROR_X13_5 = 0x93CD15AD               # ror x13, x13, #5    (StickR -> bit 0)
BFI_X13_57 = 0xB34701AD              # bfi x13, x13, #57, #1 (-> bit 62 after)
ROR_X13_59 = 0x93CDEDAD              # ror x13, x13, #59   (back in place)


def nocheats_body():
    """
    The cave body: copy StickR to bit 62 before the poll stores the button
    mask. Bit 5 itself is left alone.

    Three instructions on `x13` alone. `x13` holds the freshly read buttons and
    is dead after the store this cave displaces, so no register has to be
    saved.
    """
    return [ROR_X13_5, BFI_X13_57, ROR_X13_59]


def apply_body(buttons):
    """What the body does to a 64-bit button word (for the tests)."""
    m = (1 << 64) - 1
    x = buttons & m
    x = ((x >> 5) | (x << 59)) & m
    x = (x & ~(1 << 57)) | ((x & 1) << 57)
    x = ((x >> 59) | (x << 5)) & m
    return x
