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
the limit gauge, L3 is 3x speed. Their on-screen icons are in this module's
own .rodata (`cheat_battleboost`, `cheat_speedx3`, `cheat_speedx3_off`,
`cheat_nobattle`, `cheat_skipdialog` at 0x11AAB32..), so the module knows the
state and therefore reads the button.

Where it reads it from is not in doubt, even though the consumer itself is
hard to pin down:

  * `main` calls `nn::hid::GetNpadState` in exactly ONE place -- 0x111C028
    (FullKey) and 0x111C0C0 (Handheld), both inside the poll at 0x111BFC0.
    Checked by enumerating every caller of both imports.
  * That poll is the only writer of the 64-bit button mask, into `obj+0x20`
    (and the previous frame's copy at `obj+0x28`).
  * None of the module's 380 imports is a booster/cheat API, and the three
    subsdks decompress to graphics, audio and system libraries with no cheat
    strings in them.

So every reader of the physical buttons in this game -- including whatever
draws those icons -- reads `obj+0x20`. Clearing StickR there makes R3 invisible
to all of it.

`0x11DDAE4` is the id -> nn::hid bit table the object's accessors use, and it
maps id 0 to bit 4 (StickL) and id 1 to bit 5 (StickR). The DirectInput
emulation's key loop runs ids **2..0x13** -- it deliberately skips both stick
clicks -- so masking StickR takes nothing away from the game's own key mapping.

L3 (3x speed) is bit 4 and would be `~0x30` instead of `~0x20`; left alone
because a speed boost is a convenience rather than something an accidental
click ruins a save with.
"""
import a64 as A

# --- disable auto-run: one word ------------------------------------------
AUTORUN_SITE = 0x10D3B4C
AUTORUN_ORIG = 0x390BF909            # strb w9, [x8, #0x2fe]   KEYBUF[0x52]
NOP = 0xD503201F

# --- no cheats: mask StickR out of the button word ------------------------
# Both paths of the poll end with the same store; the FullKey one and the
# Handheld one. Both are patched, because either can be the live path
# depending on how the console is being held.
BUTTON_STORES = (0x111C04C, 0x111C0D4)
BUTTON_STORE_ORIG = 0xF900126D       # str x13, [x19, #0x20]
STICKR_BIT = 5
AND_NOT_STICKR = 0x927AF9AD          # and x13, x13, #0xffffffffffffffdf


def autorun_patch():
    """The word patch that removes the synthesised run key."""
    return [('auto-run: stick magnitude no longer holds the run button '
             '(KEYBUF[0x52])', AUTORUN_SITE, AUTORUN_ORIG, NOP)]


def nocheats_body():
    """
    The cave body: clear StickR before the poll stores the button mask.

    One instruction. `x13` holds the freshly read buttons and is dead after
    the store this cave displaces, so nothing else sees the change and no
    register has to be saved.
    """
    return [AND_NOT_STICKR]
