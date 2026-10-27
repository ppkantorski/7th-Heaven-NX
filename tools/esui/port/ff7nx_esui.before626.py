#!/usr/bin/env python3
"""ff7nx_esui.py -- [Tsunamods] Enhanced Stock UI on the Switch port, one
menu screen at a time. BUILD 621.

WHY THIS IS NOT "EMPLACE THE MOD"
The mod is 1,732 x86 .text patches plus 358 .data ones and FFNx DDS art.
The Switch runs a prebuilt ARM64 recompilation of the x86 code (exefs/main),
so a .text byte changes nothing, and FFNx external textures do not exist.
With the mod enabled, build.py hands it to this module (handles()): nothing
of it is emplaced file by file, and its options decide what is applied.

PER SCREEN (today: the main menu)
  * CODE: every changed x86 instruction of the screen's HEXT (00-Main, plus
    the option folders that patch the same code: Fullmenu = "Main Menu
    Width"; 03-Battle's two words in the shared number draw) was found in the
    port's ARM64 body of the same function, and the matching words are
    edited in place -- no caves, every word verified before it is written.
    Checked differentially (unicorn): stock vs ESUI x86 against port vs
    edited ARM64, every call argument, memory write and return value.
  * DATA: the screen's .data/.rdata patches into ff7_en (texture names
    _h -> _l, menu font scale 1.667 -> 2.0, strings).
  * ART: every menu sheet the active folders replace (fonts, windows, icons,
    cursor, avatars, colour gradients) is rebuilt as a native TEX from the
    folders' DDS, last folder winning as in 7th Heaven -- so Font Brightness,
    Cursor Style, Items/Materia icons, Border Thickness, Avatar Set, EXP bars
    pick exactly the art they pick on PC. 2x by default (BUILD 623): every
    menu draw normalises its UVs with constants (glyphs 1/256 at 0x7B7CFC,
    avatars/cursor/bars likewise), never the sheet's own size, so a bigger
    sheet is sampled at the same place; each one is also marked with its
    logical scale for the u_offset bridge. SEVENTH_NX_ESUI_TEXSCALE=1 or 4.
  * FONT WIDTHS: 00-Spacing's table, with the cave values the active option
    folders patch, into window.bin's width section -- where the game reads
    the table from, so no cave is needed.

CONTROLLER ICONS
ESUI's controller options are never used: the Switch button glyphs come from
the port's own data/png/buttons.png. Where ESUI's text set draws Xbox/PS
buttons over ordinary characters, those cells keep the game's characters.

THE ECHO-S CALENDAR
It rebuilds the same Time/Gil box 00-Main moves. On PC the pair is settled by
ESUI's `ECHOS/MAIN/05 - ESUI Theme.txt`; ff7nx_calendar carries that layout
as its ESUI profile (esui_theme()), and this pass leaves the box's words to
it whenever it is installed (calendar_owned).

NOT CARRIED (yet): every other screen (Item, Magic, Materia, Equip, Status,
Config, PHS, Shop, Save, battle, post-battle, field dialogs, world, title),
the kernel icon names, ESUI's colour file for the Time/Gil labels. uncovered()
lists any active HEXT that touches main-menu code and is not carried.

SWITCH
Mod enabled: on, every carried screen. SEVENTH_NX_ESUI=0 turns it off,
SEVENTH_NX_ESUI=main picks screens. Without the mod, SEVENTH_NX_ESUI=main
applies the code and data only (development).

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import os
import struct
import zlib
from pathlib import Path

ENV = 'SEVENTH_NX_ESUI'
SCALE_ENV = 'SEVENTH_NX_ESUI_TEXSCALE'
SCREENS = ('main', 'load', 'shop')
MOD_ID = '2b49d831-6556-41df-9ddb-d1a6e937f548'

# Set by build_plan when the [Tsunamods] Enhanced Stock UI mod is enabled:
# {'mod': Mod, 'folders': [active folders, last write wins], 'options': {...}}
CURRENT = None


def handles(mod):
    """True for the Enhanced Stock UI mod: this module applies it, file by
    file emplacement does not (its HEXT is x86 code, its art is FFNx DDS)."""
    m = getattr(mod, 'manifest', None)
    if m is None:
        return False
    if str(getattr(m, 'mod_id', '')).lower() == MOD_ID:
        return True
    return 'enhanced stock ui' in (getattr(m, 'name', '') or '').lower()


def plan_for(mod, settings, read=None, log=lambda *_: None):
    import iro
    folders = iro.active_folders(mod.manifest, settings, read=read)
    return {'mod': mod, 'folders': [f.replace('\\', '/') for f in folders],
            'options': dict(settings)}


def _env_screens():
    raw = os.environ.get(ENV, None)
    if raw is None:
        return None
    raw = raw.strip().lower()
    if raw in ('0', 'off', 'no', 'false'):
        return ()
    if raw in ('', '1', 'on', 'yes', 'true', 'all'):
        return SCREENS
    want = tuple(s.strip() for s in raw.replace(';', ',').split(',') if s.strip())
    return tuple(s for s in SCREENS if s in want)


def screens():
    """Screens applied. With the mod enabled: every screen this module
    carries (SEVENTH_NX_ESUI narrows it, or =0 turns it off). Without the
    mod: only what SEVENTH_NX_ESUI names (layout code and data only)."""
    env = _env_screens()
    if CURRENT is not None:
        return SCREENS if env is None else env
    return env or ()


def enabled():
    return bool(screens())


def unknown_screens():
    raw = os.environ.get(ENV, '').strip().lower()
    if raw in ('', '0', 'off', 'no', 'false', '1', 'on', 'yes', 'true', 'all'):
        return ()
    return tuple(s.strip() for s in raw.replace(';', ',').split(',')
                 if s.strip() and s.strip() not in SCREENS)


def folders():
    return list(CURRENT['folders']) if CURRENT else []


def has_folder(name):
    if CURRENT is None:
        # SEVENTH_NX_ESUI without the mod: the default option set
        return name in ('EnhancedStock', 'ECHOS/MAIN', 'Fullmenu')
    low = name.lower()
    return any(f.lower() == low for f in CURRENT['folders'])


def esui_theme():
    """ESUI's own Echo-S Time/Gil layout (`ECHOS/MAIN/05 - ESUI Theme`)."""
    return 'main' in screens() and has_folder('ECHOS/MAIN')


def tex_scale():
    raw = os.environ.get(SCALE_ENV, '').strip()
    try:
        v = int(raw) if raw else 2
    except ValueError:
        v = 2
    return v if v in (1, 2, 4) else 2


# BUILD 623d: the window-frame sheet stays at the game's size. Its edge
# lines (Bars/ThinBars, btl_win_c_l palette 0) are a grey core with a dark
# halo in ESUI's 4x art; at 2x the halo becomes opaque black rows above and
# below a 1-texel core, so on hardware the top edge reads as if the window
# fill covered it. At 1x the area average folds core and halo into a 2-texel
# grey line -- the look confirmed on hardware before 2x.
NATIVE_SIZE_SHEETS = ('btl_win_c_l.tex',)


# BUILD 623i: what actually dims the top edge. The window frame's pieces
# (x86 0x6E7D20: top edge uv 0..16 x 192..200 of btl_win_c_l, quads 8 units
# tall) carry ESUI's line as dark / 1-texel grey core / dark at 2x. A core of
# one 2x texel is half a unit -- 0.75 px at 720p -- so whether it shows
# depends on where the pixel grid falls: on hardware the sides hit it (121
# grey), the top straddled it (30). PC draws the 4x art at 1080p+, where the
# core is a pixel or more. Here the frame cells' grey core is grown by one
# texel (into the mid-grey row beside it) so it is a full unit wide and
# always lands on a pixel; the dark edges stay, as on PC.
# SEVENTH_NX_ESUI_FRAME_1X=1 builds the frame sheet at 1x instead (the
# single-shade look before 2x).
FRAME_REGION = (0, 188, 64, 256)        # x0, y0, x1, y1 in 1x texels


def sheet_scale(name):
    if name in NATIVE_SIZE_SHEETS and os.environ.get(
            'SEVENTH_NX_ESUI_FRAME_1X', '').strip().lower() in ('1', 'on', 'yes', 'true'):
        return 1
    return tex_scale()


def grow_frame_core(img, scale):
    """img: the 4x source RGBA. Returns it with the frame cells' grey core
    one output texel wider, at the output scale."""
    import numpy as np
    import ff7nx_ddstex as DT
    H, W = img.shape[:2]
    oh, ow = 256 * scale, 256 * scale
    r = DT._resample_area(np.ascontiguousarray(img), ow, oh).copy()
    x0, y0, x1, y1 = [v * scale for v in FRAME_REGION]
    reg = r[y0:y1, x0:x1]
    lum = reg[:, :, :3].astype(np.int32).mean(2)
    op = reg[:, :, 3] >= 128
    core = op & (lum >= 120)
    mid = op & (lum >= 40) & (lum < 120)
    grow = np.zeros_like(core)
    grow[1:, :] |= core[:-1, :] & mid[1:, :]
    grow[:-1, :] |= core[1:, :] & mid[:-1, :]
    grow[:, 1:] |= core[:, :-1] & mid[:, 1:]
    grow[:, :-1] |= core[:, 1:] & mid[:, :-1]
    if grow.any():
        # each grown texel takes the brightest core neighbour's colour
        src = reg.copy()
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            sh = np.roll(np.roll(src, dy, 0), dx, 1)
            shl = np.roll(np.roll(lum * core, dy, 0), dx, 1)
            take = grow & (shl >= 120)
            reg[take] = sh[take]
        r[y0:y1, x0:x1] = reg
    k = H // oh
    return np.repeat(np.repeat(r, k, 0), k, 1) if k > 1 else r


# ---------------------------------------------------------------- main menu
#
# (ARM64 module address, stock word, ESUI word, x86 function, how it was found)
# 'OK'/'GROUP'/'REGSWAP' = matched automatically; 'm:' = derived by hand.
# Generated by tools/esui (align.py + manual.py), checked by diffemu.py.
# CODE_MAIN = 00-Main (+ 03-Battle's two words in the shared number draw);
# CODE_FULLMENU = what the "Main Menu Width" option (folder Fullmenu)
# changes on top of it.
CODE_MAIN = [
    (0xCA5AF0, 0x52802708, 0x52802848, 0x6C98A6, 'OK'),  # mov w8, #0x138 -> mov w8, #0x142
    (0xCA6644, 0x52801488, 0x52800EA8, 0x6C98A6, 'OK'),  # mov w8, #0xa4 -> mov w8, #0x75
    (0xCA6684, 0x52803B88, 0x52803BA8, 0x6C98A6, 'OK'),  # mov w8, #0x1dc -> mov w8, #0x1dd
    (0xCE43E8, 0x321E07E9, 0x52800169, 0x6F9739, 'OK'),  # mov w9, #0xc -> mov w9, #0xb
    (0xCE43F0, 0x51003108, 0x51002D08, 0x6F9739, 'm:6f984a (x-1)*c folded'),  # sub w8, w8, #0xc -> sub w8, w8, #0xb
    (0xCE4418, 0x321F0BE9, 0x52800169, 0x6F9739, 'm:6f9860 (03-Battle) imul 0xe'),  # mov w9, #0xe -> mov w9, #0xb
    (0xCE4420, 0x51003908, 0x51002D08, 0x6F9739, 'm:6f9860 (03-Battle) folded'),  # sub w8, w8, #0xe -> sub w8, w8, #0xb
    (0xCE50D4, 0x321E07E9, 0x52800169, 0x6F9739, 'OK'),  # mov w9, #0xc -> mov w9, #0xb
    (0xCE50DC, 0x51003108, 0x51002D08, 0x6F9739, 'm:6f9a85 (x-1)*c folded'),  # sub w8, w8, #0xc -> sub w8, w8, #0xb
    (0xCE5104, 0x321F0BE9, 0x52800169, 0x6F9739, 'm:6f9a9b (03-Battle) imul 0xe'),  # mov w9, #0xe -> mov w9, #0xb
    (0xCE510C, 0x51003908, 0x51002D08, 0x6F9739, 'm:6f9a9b (03-Battle) folded'),  # sub w8, w8, #0xe -> sub w8, w8, #0xb
    (0xCEB3CC, 0x11007917, 0x11008117, 0x6C62A2, 'OK'),  # add w23, w8, #0x1e -> add w23, w8, #0x20
    (0xCEB418, 0x52800D39, 0x52800BF9, 0x6C62A2, 'GROUP'),  # mov w25, #0x69 -> mov w25, #0x5f
    (0xCEB580, 0x1100791A, 0x1100811A, 0x6C62A2, 'OK'),  # add w26, w8, #0x1e -> add w26, w8, #0x20
    (0xCEBF18, 0x11015D1A, 0x11014D1A, 0x6C62A2, 'OK'),  # add w26, w8, #0x57 -> add w26, w8, #0x53
    (0xCEC268, 0x11015D16, 0x11014D16, 0x6C62A2, 'OK'),  # add w22, w8, #0x57 -> add w22, w8, #0x53
    (0xCEC2FC, 0x320003F6, 0x528000F6, 0x6C62A2, 'GROUP'),  # mov w22, #1 -> mov w22, #0x7
    (0xCEC32C, 0xB9000018, 0xB9000017, 0x6C62A2, 'REGSWAP'),  # str w24, [x0] -> str w23, [x0]
    (0xCEC398, 0x11013D1A, 0x11012D1A, 0x6C62A2, 'OK'),  # add w26, w8, #0x4f -> add w26, w8, #0x4b
    (0xCEC454, 0xB9000018, 0xB9000017, 0x6C62A2, 'REGSWAP'),  # str w24, [x0] -> str w23, [x0]
    (0xCEC4BC, 0x11013D16, 0x11012D16, 0x6C62A2, 'OK'),  # add w22, w8, #0x4f -> add w22, w8, #0x4b
    (0xCFF228, 0x528FC7D3, 0x529DCFD3, 0x6CA1EF, 'm:cap-1 lo'),  # mov w19, #0x7e3e -> movz w19, #0xee7e
    (0xCFF22C, 0x72A000B3, 0x72A006D3, 0x6CA1EF, 'm:cap-1 hi'),  # movk w19, #5, lsl #16 -> movk w19, #0x36, lsl #16
    (0xCFF4E8, 0x528FC7D3, 0x529DCFD3, 0x6CA23F, 'm:cap-1 lo'),  # mov w19, #0x7e3e -> movz w19, #0xee7e
    (0xCFF4EC, 0x72A000B3, 0x72A006D3, 0x6CA23F, 'm:cap-1 hi'),  # movk w19, #5, lsl #16 -> movk w19, #0x36, lsl #16
    (0xCFF898, 0x528FC7D3, 0x529DCFD3, 0x6CA2A9, 'm:cap-1 lo'),  # mov w19, #0x7e3e -> movz w19, #0xee7e
    (0xCFF89C, 0x72A000B3, 0x72A006D3, 0x6CA2A9, 'm:cap-1 hi'),  # movk w19, #5, lsl #16 -> movk w19, #0x36, lsl #16
    (0xD00D10, 0x11004913, 0x11004513, 0x6CA346, 'OK+MAP2'),  # add w19, w8, #0x12 -> add w19, w8, #0x11
    (0xD00D3C, 0x528038C8, 0x52803A48, 0x6CA346, 'OK+MAP2'),  # mov w8, #0x1c6 -> mov w8, #0x1d2
    (0xD0251C, 0x11004913, 0x11004513, 0x6CA346, 'm:6ca8e9 copy'),  # add w19, w8, #0x12 -> add w19, w8, #0x11
    (0xD02548, 0x528038C8, 0x52803A48, 0x6CA346, 'm:6ca8ed copy'),  # mov w8, #0x1c6 -> mov w8, #0x1d2
    (0xD025BC, 0x528028A8, 0x52803128, 0x6CA346, 'OK'),  # mov w8, #0x145 -> mov w8, #0x189
    (0xD02608, 0x52804989, 0x52804B09, 0x6CA346, 'm:6ca952 folded'),  # mov w9, #0x24c -> mov w9, #0x258
    (0xD02688, 0x11004916, 0x11002516, 0x6CA346, 'OK'),  # add w22, w8, #0x12 -> add w22, w8, #0x9
    (0xD026B4, 0x11004116, 0x11003116, 0x6CA346, 'OK'),  # add w22, w8, #0x10 -> add w22, w8, #0xc
    (0xD026F0, 0x52800748, 0x52800548, 0x6CA346, 'OK'),  # mov w8, #0x3a -> mov w8, #0x2a
    (0xD02708, 0x52802768, 0x52801EE8, 0x6CA346, 'OK'),  # mov w8, #0x13b -> mov w8, #0xf7
    (0xD02804, 0x528061E9, 0x52806329, 0x6CA346, 'OK'),  # mov w9, #0x30f -> mov w9, #0x319
    (0xD02830, 0x52802B88, 0x52802D68, 0x6CA346, 'OK'),  # mov w8, #0x15c -> mov w8, #0x16b
    (0xD02870, 0x321F03F7, 0x52800077, 0x6CA346, 'm:6ca9d6'),  # mov w23, #2 -> mov w23, #3
    (0xD02874, 0x111A2335, 0xB9000017, 0x6CA346, 'm:6ca9d6'),  # add w21, w25, #0x688 -> str w23, [x0]
    (0xD02878, 0xB9000017, 0x52800057, 0x6CA346, 'm:6ca9d6'),  # str w23, [x0] -> mov w23, #2
    (0xD0287C, 0x2A1503E0, 0x111A2320, 0x6CA346, 'm:6ca9d6'),  # mov w0, w21 -> add w0, w25, #0x688
    (0xD028DC, 0x11004116, 0x11003916, 0x6CA346, 'OK'),  # add w22, w8, #0x10 -> add w22, w8, #0xe
    (0xD02908, 0x11010916, 0x1100C516, 0x6CA346, 'OK'),  # add w22, w8, #0x42 -> add w22, w8, #0x31
    (0xD02964, 0x1A9F3296, 0x2A1403F6, 0x6CA346, 'm:6caa11 push 7'),  # csel w22, w20, wzr, lo -> mov w22, w20
    (0xD029A4, 0x11004916, 0x11004116, 0x6CA346, 'OK'),  # add w22, w8, #0x12 -> add w22, w8, #0x10
    (0xD029D0, 0x11015D16, 0x11014116, 0x6CA346, 'OK'),  # add w22, w8, #0x57 -> add w22, w8, #0x50
    (0xD02A38, 0x2A1503E0, 0x111A2320, 0x6CA346, 'm:w21 freed'),  # mov w0, w21 -> add w0, w25, #0x688
    (0xD02A98, 0x11004116, 0x11003916, 0x6CA346, 'OK'),  # add w22, w8, #0x10 -> add w22, w8, #0xe
    (0xD02AC4, 0x11018516, 0x11016516, 0x6CA346, 'OK'),  # add w22, w8, #0x61 -> add w22, w8, #0x59
    (0xD02B3C, 0x11004916, 0x11004116, 0x6CA346, 'OK'),  # add w22, w8, #0x12 -> add w22, w8, #0x10
    (0xD02B68, 0x1101D916, 0x1101B916, 0x6CA346, 'OK'),  # add w22, w8, #0x76 -> add w22, w8, #0x6e
    (0xD02BD0, 0x2A1503E0, 0x111A2320, 0x6CA346, 'm:w21 freed'),  # mov w0, w21 -> add w0, w25, #0x688
    (0xD02C30, 0x11004115, 0x11003915, 0x6CA346, 'OK'),  # add w21, w8, #0x10 -> add w21, w8, #0xe
    (0xD02C5C, 0x11020115, 0x1101DD15, 0x6CA346, 'OK'),  # add w21, w8, #0x80 -> add w21, w8, #0x77
    (0xD02CE0, 0x11003115, 0x11002515, 0x6CA346, 'OK'),  # add w21, w8, #0xc -> add w21, w8, #0x9
    (0xD02DC4, 0x1100B915, 0x11009D15, 0x6CA346, 'OK'),  # add w21, w8, #0x2e -> add w21, w8, #0x27
    (0xD02DF0, 0x11008115, 0x11007D15, 0x6CA346, 'OK'),  # add w21, w8, #0x20 -> add w21, w8, #0x1f
    (0xD02E6C, 0x1100A915, 0x11008915, 0x6CA346, 'OK'),  # add w21, w8, #0x2a -> add w21, w8, #0x22
    (0xD02E98, 0x11001915, 0x11000915, 0x6CA346, 'OK'),  # add w21, w8, #6 -> add w21, w8, #0x2
    (0xD02ED4, 0x52800908, 0x52800848, 0x6CA346, 'OK'),  # mov w8, #0x48 -> mov w8, #0x42
    (0xD02EEC, 0x52801508, 0x52801488, 0x6CA346, 'OK'),  # mov w8, #0xa8 -> mov w8, #0xa4
    (0xD02F2C, 0x51001115, 0x51002915, 0x6CA346, 'OK'),  # sub w21, w8, #4 -> sub w21, w8, #0xa
    (0xD0339C, 0x39002308, 0x528007A9, 0x6CA346, 'm:6cac53'),  # strb w8, [x24, #8] -> mov w9, #0x3d
    (0xD033A0, 0xB9400B08, 0x1B097D1A, 0x6CA346, 'm:6cac53'),  # ldr w8, [x24, #8] -> mul w26, w8, w9
    (0xD033A4, 0x531F791A, 0x53057F5A, 0x6CA346, 'm:6cac53'),  # lsl w26, w8, #1 -> lsr w26, w26, #5
    (0xD033D0, 0x1100D91A, 0x1100C91A, 0x6CA346, 'OK'),  # add w26, w8, #0x36 -> add w26, w8, #0x32
    (0xD033FC, 0x11055D1A, 0x11052D1A, 0x6CA346, 'OK'),  # add w26, w8, #0x157 -> add w26, w8, #0x14b
    (0xD03648, 0x11017D1A, 0x11016D1A, 0x6CA346, 'OK'),  # add w26, w8, #0x5f -> add w26, w8, #0x5b
    (0xD03674, 0x1105651A, 0x11052D1A, 0x6CA346, 'OK'),  # add w26, w8, #0x159 -> add w26, w8, #0x14b
    (0xD03758, 0x1100D91A, 0x1100C91A, 0x6CA346, 'OK'),  # add w26, w8, #0x36 -> add w26, w8, #0x32
    (0xD03784, 0x1105551A, 0x11051D1A, 0x6CA346, 'OK'),  # add w26, w8, #0x155 -> add w26, w8, #0x147
    (0xD03860, 0x11017D1A, 0x11016D1A, 0x6CA346, 'OK'),  # add w26, w8, #0x5f -> add w26, w8, #0x5b
    (0xD0388C, 0x1105551A, 0x11051D1A, 0x6CA346, 'OK'),  # add w26, w8, #0x155 -> add w26, w8, #0x147
    (0xD03910, 0x11008508, 0x11007508, 0x6CA346, 'OK'),  # add w8, w8, #0x21 -> add w8, w8, #0x1d
    (0xD03954, 0x11051D1A, 0x1105211A, 0x6CA346, 'OK'),  # add w26, w8, #0x147 -> add w26, w8, #0x148
    (0xD039D0, 0x1101291A, 0x1101191A, 0x6CA346, 'OK'),  # add w26, w8, #0x4a -> add w26, w8, #0x46
    (0xD039FC, 0x11051D1A, 0x1105211A, 0x6CA346, 'OK'),  # add w26, w8, #0x147 -> add w26, w8, #0x148
    (0xD03AEC, 0x1101391A, 0x11012D1A, 0x6CA346, 'OK'),  # add w26, w8, #0x4e -> add w26, w8, #0x4b
    (0xD03FD0, 0x11013913, 0x11016513, 0x6CA346, 'OK'),  # add w19, w8, #0x4e -> add w19, w8, #0x59
    (0xD03FFC, 0x321D7BE8, 0x52800008, 0x6CA346, 'OK'),  # mov w8, #-5 -> mov w8, #0x0
    (0xDACAEC, 0x528001B6, 0x52800176, 0x6F62F7, 'm:6f6375 (port keeps 0xf for its flag-set mode)'),  # mov w22, #0xd -> mov w22, #0xb
    (0xDC1498, 0x52800348, 0x528002C8, 0x70212A, 'OK'),  # mov w8, #0x1a -> mov w8, #0x16
    (0xDC14A8, 0x1101E134, 0x1101AD34, 0x70212A, 'OK'),  # add w20, w9, #0x78 -> add w20, w9, #0x6b
    (0xDC14E0, 0x11016114, 0x11016914, 0x70212A, 'OK'),  # add w20, w8, #0x58 -> add w20, w8, #0x5a
    (0xDC156C, 0x11003515, 0x11004115, 0x70212A, 'OK'),  # add w21, w8, #0xd -> add w21, w8, #0x10
    (0xDC15A4, 0x11003915, 0x11008915, 0x70212A, 'OK'),  # add w21, w8, #0xe -> add w21, w8, #0x22
    (0xDC162C, 0x1100BD15, 0x1100A115, 0x70212A, 'OK'),  # add w21, w8, #0x2f -> add w21, w8, #0x28
    (0xDC1664, 0x11003915, 0x11008915, 0x70212A, 'OK'),  # add w21, w8, #0xe -> add w21, w8, #0x22
    (0xDC16EC, 0x11014515, 0x11014115, 0x70212A, 'OK'),  # add w21, w8, #0x51 -> add w21, w8, #0x50
    (0xDC1724, 0x11003915, 0x11008915, 0x70212A, 'OK'),  # add w21, w8, #0xe -> add w21, w8, #0x22
    (0xDC17AC, 0x1101D115, 0x1101A115, 0x70212A, 'OK'),  # add w21, w8, #0x74 -> add w21, w8, #0x68
    (0xDC186C, 0x11023514, 0x1101F914, 0x70212A, 'OK'),  # add w20, w8, #0x8d -> add w20, w8, #0x7e
    (0xDC2AB4, 0x11014515, 0x11013115, 0x70212A, 'OK+MAP2'),  # add w21, w8, #0x51 -> add w21, w8, #0x4c
    (0xDC2C30, 0x11014935, 0x11013535, 0x70212A, 'm:7022e7 lea +0x52->0x4d'),  # add w21, w9, #0x52 -> add w21, w9, #0x4d
    (0xDC2DA8, 0x0B081108, 0x5280034A, 0x70212A, 'm:70236e imul 0x22->0x1a'),  # add w8, w8, w8, lsl #4 -> mov w10, #0x1a
    (0xDC2DAC, 0x531F7908, 0x1B0A7D08, 0x70212A, 'm:70236e'),  # lsl w8, w8, #1 -> mul w8, w8, w10
    (0xDC2DB8, 0x11018114, 0x11017914, 0x70212A, 'OK'),  # add w20, w8, #0x60 -> add w20, w8, #0x5e
    (0xDC2DE8, 0x321E0BEA, 0x5280028A, 0x70212A, 'OK'),  # mov w10, #0x1c -> mov w10, #0x14
    (0xDC329C, 0x321D0BEA, 0x5280056A, 0x70212A, 'OK'),  # mov w10, #0x38 -> mov w10, #0x2b
    (0xDC3608, 0x0B081108, 0x5280034A, 0x70212A, 'm:702699 imul 0x22->0x1a'),  # add w8, w8, w8, lsl #4 -> mov w10, #0x1a
    (0xDC360C, 0x531F7908, 0x1B0A7D08, 0x70212A, 'm:702699'),  # lsl w8, w8, #1 -> mul w8, w8, w10
    (0xDC3648, 0x11012D13, 0x1100FD13, 0x70212A, 'OK'),  # add w19, w8, #0x4b -> add w19, w8, #0x3f
    (0xDC4DD4, 0x52800288, 0x528001C8, 0x703176, 'OK'),  # mov w8, #0x14 -> mov w8, #0xe
    (0xDC4E4C, 0x52800128, 0x52800188, 0x703176, 'OK'),  # mov w8, #9 -> mov w8, #0xc
    (0xDC4EE8, 0x528028E8, 0x52802A48, 0x703176, 'OK'),  # mov w8, #0x147 -> mov w8, #0x152
    (0xDC5328, 0x528002E8, 0x52800308, 0x703176, 'OK'),  # mov w8, #0x17 -> mov w8, #0x18
    (0xDC53DC, 0x52800A38, 0x52800A18, 0x703176, 'GROUP'),  # mov w24, #0x51 -> mov w24, #0x50
]

CODE_FULLMENU = [
    (0xCA5BC4, 0x321E1BF8, 0x528042B8, 0x6C98A6, 'm:6c9b44 (Fullmenu text X, loop)'),  # mov w24, #0x1fc -> mov w24, #0x215
    (0xCA61B0, 0x321E1BE8, 0x528042A8, 0x6C98A6, 'OK'),  # mov w8, #0x1fc -> mov w8, #0x215
    (0xCA6414, 0x321E1BE8, 0x528042A8, 0x6C98A6, 'OK'),  # mov w8, #0x1fc -> mov w8, #0x215
    (0xCA6514, 0x321E1BE8, 0x528042A8, 0x6C98A6, 'OK+MAP2'),  # mov w8, #0x1fc -> mov w8, #0x215
    (0xCA65EC, 0x321E1BE8, 0x528042A8, 0x6C98A6, 'OK+MAP2'),  # mov w8, #0x1fc -> mov w8, #0x215
    (0xCA6644, 0x52801488, 0x52801148, 0x6C98A6, 'OK'),  # mov w8, #0xa4 -> mov w8, #0x8a
    (0xCA6684, 0x52803B88, 0x52803EC8, 0x6C98A6, 'OK'),  # mov w8, #0x1dc -> mov w8, #0x1f6
    (0xD00D3C, 0x528038C8, 0x52803D68, 0x6CA346, 'OK+MAP2'),  # mov w8, #0x1c6 -> mov w8, #0x1eb
    (0xD02548, 0x528038C8, 0x52803D68, 0x6CA346, 'm:6ca8ed copy (Fullmenu cursor X)'),  # mov w8, #0x1c6 -> mov w8, #0x1eb
]

# BUILD 622: the port's own glyph-width special case. All three glyph
# builders (menu 0x6F564E, field dialog 0x6E706D, battle 0x6D1CC0) carry code
# the x86 does not have: characters 0x2D/0x37/0x4D/0x57 (M W m w) get an
# 18-unit quad instead of 24, and 0x73/0xCA a 16-unit cell. The u/v span
# stays 24 texels, so the port's stock sheets must hold those glyphs
# stretched; ESUI's art (drawn for the PC's 24-unit quad) comes out squashed
# to 3/4 with a gap after it ("M agic", "Lim it"). Each compare is pointed at
# 0xFFF, which no character byte equals, so every glyph takes the x86's
# 24-unit quad, as FFNx draws it. Only with ESUI's sheets in (CURRENT).
CODE_GLYPHW = [
    (0xD293E4, 0x7100B51F, 0x713FFD1F, 0x6D1CC0, 'cmp w8, #0x2d'),
    (0xD293EC, 0x7100DD1F, 0x713FFD1F, 0x6D1CC0, 'cmp w8, #0x37'),
    (0xD293F4, 0x7101351F, 0x713FFD1F, 0x6D1CC0, 'cmp w8, #0x4d'),
    (0xD2963C, 0x71015D1F, 0x713FFD1F, 0x6D1CC0, 'cmp w8, #0x57'),
    (0xD29644, 0x7101CD1F, 0x713FFD1F, 0x6D1CC0, 'cmp w8, #0x73'),
    (0xD2964C, 0x7103291F, 0x713FFD1F, 0x6D1CC0, 'cmp w8, #0xca'),
    (0xD296FC, 0x7100B51F, 0x713FFD1F, 0x6D1CC0, 'cmp w8, #0x2d'),
    (0xD29704, 0x7100DD1F, 0x713FFD1F, 0x6D1CC0, 'cmp w8, #0x37'),
    (0xD2970C, 0x7101351F, 0x713FFD1F, 0x6D1CC0, 'cmp w8, #0x4d'),
    (0xD29AD8, 0x71015D1F, 0x713FFD1F, 0x6D1CC0, 'cmp w8, #0x57'),
    (0xD29AE0, 0x7101CD1F, 0x713FFD1F, 0x6D1CC0, 'cmp w8, #0x73'),
    (0xD29AE8, 0x7103291F, 0x713FFD1F, 0x6D1CC0, 'cmp w8, #0xca'),
    (0xD875C4, 0x7100B51F, 0x713FFD1F, 0x6E706D, 'cmp w8, #0x2d'),
    (0xD875D0, 0x7100DD1F, 0x713FFD1F, 0x6E706D, 'cmp w8, #0x37'),
    (0xD875D8, 0x7101351F, 0x713FFD1F, 0x6E706D, 'cmp w8, #0x4d'),
    (0xD87648, 0x71015D1F, 0x713FFD1F, 0x6E706D, 'cmp w8, #0x57'),
    (0xD87654, 0x7101CD1F, 0x713FFD1F, 0x6E706D, 'cmp w8, #0x73'),
    (0xD87660, 0x7103291F, 0x713FFD1F, 0x6E706D, 'cmp w8, #0xca'),
    (0xD876CC, 0x7100B51F, 0x713FFD1F, 0x6E706D, 'cmp w8, #0x2d'),
    (0xD876D8, 0x7100DD1F, 0x713FFD1F, 0x6E706D, 'cmp w8, #0x37'),
    (0xD876E0, 0x7101351F, 0x713FFD1F, 0x6E706D, 'cmp w8, #0x4d'),
    (0xD87728, 0x71015D1F, 0x713FFD1F, 0x6E706D, 'cmp w8, #0x57'),
    (0xD87734, 0x7101CD1F, 0x713FFD1F, 0x6E706D, 'cmp w8, #0x73'),
    (0xD87740, 0x7103291F, 0x713FFD1F, 0x6E706D, 'cmp w8, #0xca'),
    (0xDAB15C, 0x7100B51F, 0x713FFD1F, 0x6F564E, 'cmp w8, #0x2d'),
    (0xDAB164, 0x7100DD1F, 0x713FFD1F, 0x6F564E, 'cmp w8, #0x37'),
    (0xDAB16C, 0x7101351F, 0x713FFD1F, 0x6F564E, 'cmp w8, #0x4d'),
    (0xDAB1CC, 0x71015D1F, 0x713FFD1F, 0x6F564E, 'cmp w8, #0x57'),
    (0xDAB1D4, 0x7101CD1F, 0x713FFD1F, 0x6F564E, 'cmp w8, #0x73'),
    (0xDAB1DC, 0x7103291F, 0x713FFD1F, 0x6F564E, 'cmp w8, #0xca'),
    (0xDAB268, 0x7100B51F, 0x713FFD1F, 0x6F564E, 'cmp w8, #0x2d'),
    (0xDAB270, 0x7100DD1F, 0x713FFD1F, 0x6F564E, 'cmp w8, #0x37'),
    (0xDAB278, 0x7101351F, 0x713FFD1F, 0x6F564E, 'cmp w8, #0x4d'),
    (0xDAB284, 0x71015D1F, 0x713FFD1F, 0x6F564E, 'cmp w8, #0x57'),
    (0xDAB28C, 0x7101CD1F, 0x713FFD1F, 0x6F564E, 'cmp w8, #0x73'),
    (0xDAB294, 0x7103291F, 0x713FFD1F, 0x6F564E, 'cmp w8, #0xca'),
]

# BUILD 623: the party block centred in the big main-menu box (asked for on
# hardware; FFNx/ESUI leave it 20 units high: 45 above the first row, 63 below
# the last). Every party draw takes its y from one row base, x86 6CABBB
# `add edx, 0x24` (ARM +0xD03150); the two party cursors have their own row
# bases (select 6CA912 / +0xD00DB0, order ghost 6CA8C3 / +0xD03FD0, which
# 00-Main already moves to 0x59). +20 on all three. SEVENTH_NX_ESUI_CENTER=0
# keeps ESUI's own position.
CENTER_DY = 20
CODE_CENTER = [
    (0xD03150, 0x1100911A, 0x1100E11A, 0x6CA346, 'party row base 0x24 -> 0x38'),
    (0xD00DB0, 0x11016913, 0x1101B913, 0x6CA346, 'select cursor 0x5a -> 0x6e'),
    (0xD03FD0, 0x11013913, 0x1101B513, 0x6CA346, 'order ghost cursor 0x59 -> 0x6d'),
    # BUILD 623e: back-row avatar offset 0x30 -> 0x20, so a back-row avatar's
    # centre sits on a front-row avatar's right edge (the visible portrait is
    # 64 wide). x86 6CABE2 `imul ecx, ecx, 0x30`; the port builds b*48 as
    # bfi (3b) then lsl #4 -> drop the bfi, lsl #5.
    (0xD031A8, 0x331F0108, 0xD503201F, 0x6CA346, 'back row offset: no bfi'),
    (0xD031AC, 0x531C6D1A, 0x531B691A, 0x6CA346, 'back row offset: lsl #5 (32)'),
    # BUILD 623f: Gil's value 1 unit further left than 00-Main's 0x1f, so it
    # ends 8 from the Time box's right edge like the labels' 8 from the left
    (0xD02DF0, 0x11008115, 0x11007915, 0x6CA346, 'Gil amount x 0x20 -> 0x1e'),
]
CENTER_ENV = 'SEVENTH_NX_ESUI_CENTER'


# Words in .rodata that the port reads where the x86 had an immediate.
RAW_MAIN = [
    (0x11b0fb0, 0x1a4, 0x1b0, '7032fd push 0x1a4->0x1b0'),
]

# ff7_en .data/.rdata: (x86 VA, stock bytes, ESUI bytes, what)
DATA_MAIN = [
    (0x7B7CF8, bytes.fromhex('5555d53f'), bytes.fromhex('00000040'),
     'menu font scale 1.667 -> 2.0'),
    (0x919DA5, b'h', b'l', 'usfont_a_h.tim -> _l'),
    (0x919DC5, b'h', b'l', 'usfont_b_h.tim -> _l'),
    (0x919DE6, b'h', b'l', 'btl_win_a_h.tim -> _l'),
    (0x919E06, b'h', b'l', 'btl_win_b_h.tim -> _l'),
    (0x919E26, b'h', b'l', 'btl_win_c_h.tim -> _l'),
    (0x919E46, b'h', b'l', 'btl_win_d_h.tim -> _l'),
    (0x919E63, b'h', b'l', 'usfont_h.tim -> _l'),
    (0x919E84, b'h', b'l', 'btl_win_h.tim -> _l'),
    (0x91AA00,
     bytes.fromhex('4e455854004c4556454cff000000000000000000'
                   '2c494d4954004c4556454cff'),
     bytes.fromhex('2e455854002c4556454cff000000000000000000'
                   '2c494d4954002c4556454cff'),
     'strings "Next Level" / "Limit Level" (FF7 text, mixed case)'),
]

# 02-Shop's strings (FF7 text, 0x00 is a space): ESUI drops the padding that
# centred them in the stock boxes -- "Exit" no longer runs into the frame.
DATA_SHOP = [
    (0x922A4C, bytes.fromhex('00000000344f004e455854004c455645'),
     bytes.fromhex('000000002e455854004c4556454cff00'), 'shop "Next Level"'),
    (0x922A5C, bytes.fromhex('4cff0000000000002d2133342532ff00'),
     bytes.fromhex('00000000000000002d2133342532ff00'), 'shop "Next Level" / "Master"'),
    (0x922CF8, bytes.fromhex('21ff24ff000000002255590000003345'),
     bytes.fromhex('21ff24ff00000000225559000033454c'), 'shop "Buy" / "Sell"'),
    (0x922D08, bytes.fromhex('4c4c000000000025584954ff27494cff'),
     bytes.fromhex('4c000025584954ff0000000027494cff'), 'shop "Sell" / "Exit"'),
    (0x922D78, bytes.fromhex('2954454d0000000000002d4154455249'),
     bytes.fromhex('2954454d00002d415445524941ff0000'), 'shop "Item" / "Materia"'),
]

# 00-Spacing's table (x86 0x99DDA8, 256 bytes), as ESUI writes it.
WIDTHS = bytes.fromhex(
    '03020305050606020303040604030204'
    '05050505050505050505020406050605'
    '07060606060505070602050605070607'
    '06070606060606090606060304030506'
    '04050505050503050502020402070505'
    '05050305030505070505050302030600'
    '06060605060706050505050505050505'
    '05050202030305050505050505050505'
    '04080804060600050500000000000900'
    '28000009090808080807070707060800'
    '05030000000000252507000606070908'
    '05080403020200000506000023230909'
    '00030000000605060505030303030707'
    '00070606000000000000000000000000'
    '01010101010101010101010101010101'
    '01010101010101010101010101010101')

def _sets():
    out = {'code': [], 'raw': [], 'data': [], 'widths': False}
    if 'main' in screens():
        code = {r[0]: r for r in CODE_MAIN}
        if has_folder('Fullmenu'):
            code.update({r[0]: r for r in CODE_FULLMENU})
        if CURRENT is not None:
            code.update({r[0]: r for r in CODE_GLYPHW})
        if os.environ.get(CENTER_ENV, '1').strip().lower() not in ('0', 'off', 'no', 'false'):
            code.update({r[0]: r for r in CODE_CENTER})
        out['code'] = [code[k] for k in sorted(code)]
        out['raw'] = list(RAW_MAIN)
        out['data'] = list(DATA_MAIN)
        out['widths'] = CURRENT is not None
    if 'shop' in screens() and CURRENT is not None:
        out['data'] += DATA_SHOP
    return out


def calendar_owned(text=None):
    """Module words ff7nx_calendar wrote, if it is installed in `text`.

    The Echo-S calendar rebuilds the main menu's Time/Gil box, which is the
    same box 00-Main moves. On PC the pair is resolved by ESUI's own
    `ECHOS/MAIN/05 - ESUI Theme.txt`; here ff7nx_calendar carries that layout
    as its ESUI profile, so when it is installed those words are its. Decided
    from the module itself (its Gil-label hook is a `bl` into its cave, not
    the stock call), because the calendar fails closed and may not be there
    even when it was asked for. This pass runs after it.
    """
    try:
        import ff7nx_calendar as C
    except ImportError:
        return set()
    if text is None or len(text) < C.DATE_HOOK + 4:
        return set()
    if struct.unpack_from('<I', text, C.DATE_HOOK)[0] == C.DATE_HOOK_ORIG:
        return set()
    return C.owned_sites()


def _hex(word):
    return ' '.join('%02X' % b for b in struct.pack('<I', word))


# ---------------------------------------------------------------- exefs/main
def module_words(text=None):
    """{module address: (stock word, new word, label)} for the enabled
    screens, given the module's .text (to see whether the calendar is in)."""
    s = _sets()
    words = {}
    skip = calendar_owned(text)
    for va, old, new, fn, note in s['code']:
        if va in skip:
            continue
        words[va] = (old, new, 'ESUI fn %#x %s' % (fn, note))
    for va, old, new, note in s['raw']:
        words[va] = (old, new, 'ESUI table %s' % note)
    return words


# ---------------------------------------------------------------- caves
# BUILD 623h. ESUI's code caves live in the ARM body of x86 0x6AF19C: 45 KB
# that nothing reaches (no branch, no relocation but its own map record, and
# its x86 address appears nowhere in the exe -- no call, no pointer). Proven
# again on every build by ff7nx_fieldbg._liveness before anything is written.
DEAD_X86 = 0x6AF19C
DEAD_GUARD = 0x40                 # never at the entry the liveness proof checks
TRANSLATE = 0x10FC3A0
X86_DRAW_GOBJ = 0x66E641          # engine_draw_graphics_object(gobj, game_obj)

# The window frame drawn over the translucent window fill. The menu draws its
# opaque objects first (window fill 0xDC0FF0, then the btl_win sheets -- the
# frame is btl_win_c, 0xDC0FD0) and its blended ones after (0xDC0FF4/0xDC0FF8,
# the translucent fills). FFNx z-sorts every menu draw (gl/special_case.cpp:
# "z-sort by default in menu"), so on PC the frame lands on top; here the
# translucent fill covers whatever frame line lies inside it -- ESUI's top
# edge line sits 4 units into its cell, under the fill (on hardware: 121 grey
# on the sides, 30 on the top). The frame object is drawn once more right
# after the blended fills (x86 6CCC7D's call, ARM +0xD0EF8C), so it is on top
# as on PC. Same vertices, same texture; only the order changes.
# RETIRED (623i): the field draws its frame AFTER its fills and shows the
# same dim top edge, so order is not the cause; see FRAME_LINE_FIX.
FRAME_HOOK = 0xD0EF90             # `ldr w8, [x21, #0x10]` after that call
FRAME_HOOK_WORD = 0xB94012A8
FRAME_GOBJ = 0xDC0FD0
FRAME_ENV = 'SEVENTH_NX_ESUI_FRAME_ON_TOP'


def _mov32(rd, v):
    import a64 as A
    v &= 0xFFFFFFFF
    out = [A.movz(rd, v & 0xFFFF)]
    if v >> 16:
        out.append(A.movk_hi(rd, v >> 16))
    return out


def _frame_cave(at, draw_arm):
    """Words of the frame-on-top cave at `at` (x21 = guest context)."""
    import a64 as A
    w = []
    pc = lambda: at + 4 * len(w)

    def push_from(load):
        # value -> host stack; esp -= 4; [esp] = value
        w.extend(load)
        w.append(A.stp64_pre(9, 10, 31, -16))
        w.extend([A.ldr(8, 21, 0x10), A.sub_imm(0, 8, 4), A.str_(0, 21, 0x10)])
        w.append(A.bl(pc(), TRANSLATE))
        w.append(A.ldp64_post(9, 10, 31, 16))
        w.append(A.str_(9, 0, 0))
    # the previous call's `add esp, 8`
    w.extend([A.ldr(8, 21, 0x10), A.add_imm(8, 8, 8), A.str_(8, 21, 0x10)])
    # push [ebp-4] (game object), push [0xDC0FD0] (the frame object)
    w.extend([A.ldr(0, 21, 0x14), A.sub_imm(0, 0, 4)])
    w.append(A.bl(pc(), TRANSLATE))
    push_from([A.ldr(9, 0, 0)])
    w.extend(_mov32(0, FRAME_GOBJ))
    w.append(A.bl(pc(), TRANSLATE))
    push_from([A.ldr(9, 0, 0)])
    # return slot, call; the callee pops the slot, the game's own `add esp,8`
    # that follows the hook pops our two arguments
    w.extend([A.ldr(8, 21, 0x10), A.sub_imm(8, 8, 4), A.str_(8, 21, 0x10)])
    w.append(A.bl(pc(), draw_arm))
    w.append(FRAME_HOOK_WORD)
    w.append(A.b(pc(), FRAME_HOOK + 4))
    return w


def cave_words(src_main, text):
    """{va: (old, new, label)} for ESUI's caves, or {} when none is wanted.
    Raises when the dead body is not provably dead in this module."""
    if True:      # BUILD 623i: retired, see FRAME_HOOK's note
        return {}
    if os.environ.get(FRAME_ENV, '1').strip().lower() in ('0', 'off', 'no', 'false'):
        return {}
    import nxmap
    import ff7nx_fieldbg
    m = nxmap.Main(src_main)
    why = ff7nx_fieldbg._liveness(m, DEAD_X86)
    if why:
        raise ValueError('x86 0x%X is not dead in this module: %s' % (DEAD_X86, why))
    lo, hi = m.extent(DEAD_X86)
    at = lo + DEAD_GUARD
    if struct.unpack_from('<I', text, FRAME_HOOK)[0] != FRAME_HOOK_WORD:
        raise ValueError('frame hook +0x%X is not the stock word' % FRAME_HOOK)
    words = _frame_cave(at, m.x86_to_arm[X86_DRAW_GOBJ])
    if at + 4 * len(words) > hi:
        raise ValueError('cave does not fit')
    out = {}
    for k, wd in enumerate(words):
        va = at + 4 * k
        out[va] = (struct.unpack_from('<I', text, va)[0], wd, 'ESUI cave: frame on top')
    import a64 as A
    out[FRAME_HOOK] = (FRAME_HOOK_WORD, A.b(FRAME_HOOK, at), 'ESUI: window frame drawn over the translucent fill')
    return out


# ---------------------------------------------------------------- ported screens
# BUILD 624: screens beyond the main menu, from ff7nx_esui_ports (generated,
# emulator-verified) laid out as caves by ff7nx_esuicave. Patrick's own
# alignment on top of ESUI's layout (TWEAKS) is applied over the port words:
#   load: the slot avatars centred in the slot's left area (ESUI x 0x29 -> 0x2B,
#         y 0x13 -> 0x16); the Time/Gil values right-aligned with the same gap
#         as the labels' left gap (hours/colon/minutes/gil x +9).
TWEAKS = {
    'load': {
        0xE3FD14: 0x1100591B,     # avatar y 0x16
        0xE3FD44: 0x1100AD1B,     # avatar x 0x2b
        # Time and Gil end 10 px (screen) inside the box's right border, the
        # gap the labels keep on the left (BUILD 626, measured on hardware)
        0xE3FF34: 0x1101E91A,     # colon x 0x7a
        0xE40030: 0x11016D1A,     # hours x 0x5b
        0xE4012C: 0x11020919,     # minutes x 0x82
        0xE4020C: 0x1100AD19,     # gil x 0x2b (ESUI 0x22)
        # Level value a little right of ESUI's (the label keeps ESUI's y 0x32)
        0xE3FE98: 0x1105AD1A,     # level value x 0x16b (stock 0x168)
    },
}


def port_words(src_main, text, log=lambda *_: None):
    """{va: (old, new, label)} for every enabled ported screen, caves in the
    dead body of x86 0x6AF19C. {} when nothing is ported or enabled."""
    try:
        import ff7nx_esui_ports as PP
    except ImportError:
        return {}
    want = [s for s in screens() if s in PP.PORTS]
    if not want or CURRENT is None:
        return {}
    import nxmap
    import ff7nx_esuicave as EC
    import ff7nx_fieldbg
    m = nxmap.Main(src_main)
    why = ff7nx_fieldbg._liveness(m, EC.DEAD_X86)
    if why:
        raise ValueError('x86 0x%X is not dead in this module: %s' % (EC.DEAD_X86, why))
    lo, hi = m.extent(EC.DEAD_X86)
    at = lo + EC.DEAD_GUARD
    rw = lambda va: struct.unpack_from('<I', text, va)[0]          # noqa: E731
    out = {}
    for screen in want:
        for fn, port in sorted(PP.PORTS[screen].items()):
            words, at = EC.build(port, rw, m.x86_to_arm, at)
            for va, new in words.items():
                out[va] = new
        for va, new in TWEAKS.get(screen, {}).items():
            out[va] = new
        log('  %s: %d function(s) ported' % (screen, len(PP.PORTS[screen])))
    if at > hi - 0x100:
        raise ValueError('ported screens need %d bytes of cave, the dead body has %d'
                         % (at - lo, hi - lo))
    return {va: (rw(va), new, 'ESUI port') for va, new in out.items()}


def apply_to_nso(src, dest, log=lambda *_: None):
    import nso_patcher
    try:
        nso = nso_patcher.read_nso(Path(src))
        text = next(g.data for g in nso.segments if g.name == '.text')
        words = module_words(text)
        for va, w in port_words(src, text, log).items():
            if va in words and words[va][1] != w[1]:
                raise ValueError('+0x%X claimed by the main menu and a ported screen' % va)
            words[va] = w
        if not words:
            return False
        # A rerun on this pass's own output: words already set are skipped.
        def word(va):
            seg, off = nso_patcher.segment_for_va(nso, va, 4)
            return struct.unpack_from('<I', seg.data, off)[0]
        done = {va for va, (old, new, _l) in words.items()
                if word(va) == new != old}
        if done and len(done) == len(words):
            log('  all %d words already in place' % len(words))
            data = open(src, 'rb').read()
            os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
            with open(dest, 'wb') as f:
                f.write(data)
            return True
        if done:
            raise ValueError('%d of %d words are already ESUI and the rest are '
                             'not -- a half-patched module' % (len(done), len(words)))
        if calendar_owned(text):
            log('  the Echo-S calendar owns the Time/Gil box (its ESUI '
                'profile); %d of its words are left to it'
                % len(calendar_owned(text) & {w[0] for w in _sets()['code']}))
        applied = nso_patcher.apply_spec(nso, {
            'name': 'Enhanced Stock UI (%s)' % ', '.join(screens()),
            'patches': [{'name': label, 'va': va, 'expect': _hex(old),
                         'set': _hex(new)}
                        for va, (old, new, label) in sorted(words.items())],
        })
        data = nso_patcher.rebuild(nso)
    except Exception as exc:                                  # noqa: BLE001
        log('! Enhanced Stock UI: %s' % exc)
        log('  nothing was written; the module is unchanged')
        return False
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(data)
    log('  %d words in exefs/main' % len(applied))
    return True


# ---------------------------------------------------------------- ff7_en
def patch_exe(data, va_to_offset, log=lambda *_: None):
    """Apply the enabled screens' .data/.rdata patches. Each site must hold
    the stock bytes or already the ESUI ones (a rerun); anything else is
    refused whole."""
    out = bytearray(data)
    todo = []
    for va, old, new, what in _sets()['data']:
        off = va_to_offset(va)
        if off is None:
            raise ValueError('%#x is not in ff7_en' % va)
        cur = bytes(out[off:off + len(old)])
        if cur == new:
            continue
        if cur != old:
            raise ValueError('%s at %#x: have %s, expected %s'
                             % (what, va, cur.hex(), old.hex()))
        todo.append((off, new))
    for off, new in todo:
        out[off:off + len(new)] = new
    return bytes(out), len(todo)


# ---------------------------------------------------------------- window.bin
def _sections(data):
    out = []
    o = 0
    while o + 6 <= len(data):
        cs, ds, idx = struct.unpack_from('<HHH', data, o)
        if cs == 0:
            break
        blob = data[o + 6:o + 6 + cs]
        raw = zlib.decompress(blob, 31)
        if len(raw) != ds:
            raise ValueError('window.bin section %d: %d bytes, header says %d'
                             % (len(out), len(raw), ds))
        out.append([idx, raw, data[o:o + 6 + cs]])
        o += 6 + cs
    return out, data[o:]


def _gzip(raw):
    c = zlib.compressobj(9, zlib.DEFLATED, 31)
    return c.compress(raw) + c.flush()


def patch_window_bin(data, table=None):
    """(new bytes, changed?) -- section 3's first 256 bytes become `table`
    (default: 00-Spacing's own WIDTHS)."""
    table = WIDTHS if table is None else table
    if len(table) != 256:
        raise ValueError('width table is %d bytes, not 256' % len(table))
    secs, tail = _sections(data)
    if len(secs) != 3 or len(secs[2][1]) < 256:
        raise ValueError('window.bin: expected three sections, the third '
                         'holding the width table')
    raw = secs[2][1]
    if raw[:256] == table:
        return bytes(data), False
    new = bytes(table) + raw[256:]
    blob = _gzip(new)
    if len(blob) > 0xFFFF:
        raise ValueError('window.bin: width section too large')
    secs[2][2] = struct.pack('<HHH', len(blob), len(new), secs[2][0]) + blob
    return b''.join(s[2] for s in secs) + tail, True


# ---------------------------------------------------------------- widths
SPACING_CAVE = (0x914000, 0x914500)
WIDTH_TABLE = 0x99DDA8


def _find(root, *parts):
    """Case-insensitive path below root."""
    path = root
    for part in parts:
        try:
            hit = next((e for e in os.listdir(path) if e.lower() == part.lower()),
                       None)
        except OSError:
            return None
        if hit is None:
            return None
        path = os.path.join(path, hit)
    return path


def _hext_files(root, folder):
    d = _find(root, *folder.split('/'), 'hext')
    if not d or not os.path.isdir(d):
        return []
    return [os.path.join(d, f) for f in sorted(os.listdir(d))
            if f.lower().endswith(('.txt', '.hext'))]


def widths(log=lambda *_: None):
    """The 256-byte character width table ESUI ends up with: 00-Spacing's
    table, then what its new-game cave writes (the cave is what persists),
    with the cave's immediates as the active option folders patch them
    (Echo-S's `00 - ESUI_Spacing`, the avatar sets' "no mini avatars")."""
    import exe_patch
    if CURRENT is None:
        return WIDTHS
    root = CURRENT['mod'].cache
    sp = _find(root, 'EnhancedStock', 'hext', '00-Spacing.txt')
    if sp is None:
        raise ValueError('EnhancedStock/hext/00-Spacing.txt not in the mod')
    mem = {}
    for va, b in exe_patch.parse_hext(open(sp, encoding='utf-8',
                                            errors='replace').read()):
        for k, x in enumerate(bytes(b)):
            mem[va + k] = x
    lo, hi = SPACING_CAVE
    for folder in CURRENT['folders']:
        if folder.lower() == 'enhancedstock':
            continue
        for h in _hext_files(root, folder):
            for va, b in exe_patch.parse_hext(open(h, encoding='utf-8',
                                                   errors='replace').read()):
                for k, x in enumerate(bytes(b)):
                    if lo <= va + k < hi:
                        mem[va + k] = x
    table = bytearray(mem.get(WIDTH_TABLE + i, 0) for i in range(256))
    stores = 0
    for at in range(lo + 0x10, hi, 0x10):
        if mem.get(at) == 0xC7 and mem.get(at + 1) == 0x05:
            dst = sum(mem.get(at + 2 + i, 0) << (8 * i) for i in range(4))
            for i in range(4):
                if 0 <= dst + i - WIDTH_TABLE < 256:
                    table[dst + i - WIDTH_TABLE] = mem.get(at + 6 + i, 0)
            stores += 1
    if stores < 60:
        raise ValueError('00-Spacing cave: only %d stores found' % stores)
    return bytes(table)


# ---------------------------------------------------------------- textures
TEX_VERSION = b'esui-tex-6'
MENU_ARCHIVE = 'menu_us.lgp'
CONTROLLER_TEXT = ('text/text-xbox', 'text/text-psx', 'text/text-new-psx')
# usfont_a_l cells (256px sheet units) where ESUI's controller text sets
# draw Xbox/PlayStation buttons over ordinary characters. The Switch's
# button glyphs are its own (data/png/buttons.png), so these cells keep the
# game's characters. Measured: Text-XBOX vs Text-PSX palette 0 differ only
# here.
CONTROLLER_CELLS = ((144, 168, 72, 120), (168, 192, 0, 240),
                    (216, 240, 24, 72))
_DDS_RE = None
# The title screen is its own screen (ESUI Start Screen), not this one.
TITLE_SHEETS = ('buster',)


def texture_sources(log=lambda *_: None):
    """{tex entry name: {palette: (folder, iro path)}}, last folder wins."""
    import re
    import iro
    global _DDS_RE
    if CURRENT is None:
        return {}
    if _DDS_RE is None:
        _DDS_RE = re.compile(r'^(.+)_([0-9]{2})\.dds$', re.I)
    ents = iro.list_entries(CURRENT['mod'].path)
    names = [e if isinstance(e, str) else e[0] for e in ents]
    low = {}
    for n in names:
        low.setdefault(n.replace('\\', '/').lower(), n)
    out = {}
    for folder in CURRENT['folders']:
        # BUILD 623g: Field/FieldESUI's art (btl_win_b_l palette 12, the field
        # countdown timer's digits) is drawn for FieldESUI's own timer code,
        # which is not carried: under the stock timer it samples the wrong
        # part of the cell (digits squashed, a neighbour's hatching showing).
        # The field screen's art comes with the field screen.
        if folder.lower().startswith('field/'):
            continue
        pre = folder.lower().rstrip('/') + '/menu/'
        for l, n in low.items():
            if not l.startswith(pre):
                continue
            m = _DDS_RE.match(l[len(pre):])
            if not m or '/' in m.group(1) or m.group(1) in TITLE_SHEETS:
                continue
            out.setdefault(m.group(1) + '.tex', {})[int(m.group(2))] = (folder, n)
    return out


def _controller_mask(h, w):
    import numpy as np
    m = np.zeros((h, w), bool)
    for y0, y1, x0, x1 in CONTROLLER_CELLS:
        m[y0 * h // 256:y1 * h // 256, x0 * w // 256:x1 * w // 256] = True
    return m


def joint_tex(vanilla, imgs, scale=1, k=255, iters=12, seed=0):
    """A multi-palette TEX whose index map serves every palette's picture.

    ESUI (like any FFNx pack) draws DIFFERENT things in different palettes of
    one sheet -- btl_win_b's slot reels only in 11, its stars only in 3..10,
    its digits in all of them -- and a TEX has one index map for all 16.
    ff7nx_ddstex.convert_group derives that map from one reference frame,
    which is right for a cycling animation and loses everything the other
    frames draw elsewhere. Here every texel is described by its colour in
    every palette at once and the 255 index slots are k-means clusters of
    those 16-palette descriptions; slot 0 stays clear in every palette.
    Palette p's colour for a slot is the mean of its members' colour in p,
    clear where most members are clear in p. Layout: vanilla's (same
    palettes, colour key and index size), widened to 256 colours as every
    converted multi-palette TEX in this build already is."""
    import numpy as np
    import ff7nx_ddstex as DT
    import tex
    t = tex.parse(vanilla)
    n_pal, cpp = t['num_palettes'], t['colors_per_palette']
    W, H = t['width'] * scale, t['height'] * scale
    vpal = np.frombuffer(t['palette'], np.uint8).reshape(n_pal, cpp, 4)
    rgb = np.zeros((n_pal, H * W, 3), np.float32)
    on = np.zeros((n_pal, H * W), bool)
    for p in range(n_pal):
        a = DT._resample_area(np.ascontiguousarray(imgs[p]), W, H)
        rgb[p] = a[:, :, :3].reshape(-1, 3)
        on[p] = a[:, :, 3].reshape(-1) >= 128
    any_on = on.any(0)
    feat = np.concatenate([(rgb * on[:, :, None]).transpose(1, 0, 2).reshape(H * W, -1),
                           on.T.astype(np.float32) * 160.0], 1)
    X = feat[any_on]
    uniq, inv, cnt = np.unique(X.astype(np.int16), axis=0, return_inverse=True,
                               return_counts=True)
    U = uniq.astype(np.float64)
    w = cnt.astype(np.float64)
    rng = np.random.default_rng(seed)
    k = min(k, len(U))

    def d2(A, C):
        # float64 + errstate: Accelerate's matmul (macOS) raises spurious
        # divide/overflow/invalid flags on float32 even for finite inputs.
        with np.errstate(all='ignore'):
            r = (A * A).sum(1)[:, None] - 2.0 * (A @ C.T) + (C * C).sum(1)[None, :]
        r = np.nan_to_num(r, nan=np.inf, posinf=np.inf, neginf=0.0)
        return np.maximum(r, 0.0)
    # k-means++ on a weighted sample
    sample = rng.choice(len(U), size=min(len(U), 20000), replace=False,
                        p=w / w.sum())
    S = U[sample]
    C = [S[rng.integers(len(S))]]
    dmin = d2(S, np.array(C))[:, 0]
    for _ in range(1, k):
        pr = np.where(np.isfinite(dmin), np.maximum(dmin, 0), 0.0)
        if pr.sum() <= 0:
            break
        c = S[rng.choice(len(S), p=pr / pr.sum())]
        C.append(c)
        dmin = np.minimum(dmin, d2(S, c[None, :])[:, 0])
    C = np.array(C)
    for _ in range(iters):
        lab = np.empty(len(U), np.int64)
        for i in range(0, len(U), 8192):
            lab[i:i + 8192] = d2(U[i:i + 8192], C).argmin(1)
        for j in range(len(C)):
            m = lab == j
            if m.any():
                C[j] = (U[m] * w[m, None]).sum(0) / w[m].sum()
    idx = np.zeros(H * W, np.uint8)
    idx[any_on] = (lab[inv.reshape(-1)] + 1).astype(np.uint8)
    out = np.zeros((n_pal, 256, 4), np.uint8)
    for p in range(n_pal):
        nz = vpal[p][:, 3][vpal[p][:, 3] > 0]
        opaque_a = 254 if (len(nz) and (nz == 254).mean() >= 0.5) else 255
        sel = idx > 0
        lab_p = idx[sel].astype(np.int64)
        tot = np.bincount(lab_p, minlength=256).astype(np.float64)
        onp = on[p][sel]
        n_on = np.bincount(lab_p, weights=onp, minlength=256)
        for c in range(3):
            s_ = np.bincount(lab_p, weights=rgb[p][sel][:, c] * onp, minlength=256)
            out[p, :, 2 - c] = np.clip(np.rint(s_ / np.maximum(n_on, 1)), 0, 255)
        out[p, :, 3] = np.where((n_on * 2 > tot) & (tot > 0), opaque_a, 0)
        out[p, 0] = 0
    hdr = bytearray(vanilla[:tex.HEADER_LEN])
    struct.pack_into('<I', hdr, tex.O_WIDTH, W)
    struct.pack_into('<I', hdr, tex.O_HEIGHT, H)
    old_pitch = struct.unpack_from('<I', vanilla, tex.O_PITCH)[0]
    if old_pitch:
        struct.pack_into('<I', hdr, tex.O_PITCH, old_pitch * scale)
    struct.pack_into('<I', hdr, tex.O_COLORS_PER_PAL, 256)
    struct.pack_into('<I', hdr, tex.O_COLORS_PER_PAL2, 256)
    struct.pack_into('<I', hdr, tex.O_PAL_SIZE, n_pal * 256)
    if struct.unpack_from('<I', vanilla, tex.O_COLORKEY)[0] == 0:
        struct.pack_into('<I', hdr, tex.O_COLORKEY, 1)
    data = bytes(hdr) + out.tobytes() + idx.tobytes()
    chk = tex.parse(data)
    if chk is None or chk['num_palettes'] != n_pal or chk['width'] != W:
        raise ValueError('joint TEX does not parse back')
    if scale > 1:
        data = tex.mark_logical_scale(data, scale)
    err = []
    for p in range(n_pal):
        m = on[p]
        if m.any():
            pr = out[p][idx][:, [2, 1, 0]].astype(np.float32)
            err.append(float(np.abs(pr[m] - rgb[p][m]).mean()))
    note = ('%dx%d -> %dx%d x%d, %d palettes x 256 colours (joint, %d slots), '
            'mean colour error %.1f' % (t['width'], t['height'], W, H, scale,
                                         n_pal, len(C), float(np.mean(err)) if err else 0))
    return data, note


def build_texture(name, vanilla, srcs, scale, log=lambda *_: None):
    """One menu TEX from its ESUI DDS set. Missing palettes: the game's own
    (font sheets: ESUI's shape in the game's colours, as SYW minigames do)."""
    import numpy as np
    import iro
    import ff7nx_ddstex as DT
    import tex
    t = tex.parse(vanilla)
    n_pal = t['num_palettes']
    imgs = {}
    template = vanilla
    for p, (folder, rel) in srcs.items():
        if p < n_pal:
            imgs[p] = DT._decode(iro.read_one(CURRENT['mod'].path, rel))[0].copy()
    if not imgs:
        return None, 'no palette this TEX has'
    ref_p = 0 if 0 in imgs else min(imgs)
    ref = imgs[ref_p]
    H, W = ref.shape[:2]
    font = name.startswith('usfont')
    for p in range(n_pal):
        if p not in imgs:
            imgs[p] = (DT.recolour_palette(vanilla, ref, p, ref_palette=ref_p)
                       if font else DT.vanilla_palette_rgba(vanilla, p, W, H))
    if name == 'usfont_a_l.tex' and any(
            srcs[p][0].lower().startswith(CONTROLLER_TEXT) for p in srcs):
        mask = _controller_mask(H, W)
        for p in range(n_pal):
            imgs[p][mask] = DT.vanilla_palette_rgba(vanilla, p, W, H)[mask]
    # A sheet the game draws opaque (no colour key, every palette entry
    # opaque) whose ESUI art has a real silhouette -- the avatars: FFNx draws
    # the DDS with its alpha, so here index 0 becomes a keyed clear texel
    # rather than the premultiplied black an opaque TEX would show.
    pal_a = np.frombuffer(t['palette'], np.uint8).reshape(-1, 4)[:, 3]
    if (struct.unpack_from('<I', vanilla, tex.O_COLORKEY)[0] == 0
            and pal_a.min() == 255 and DT.alpha_is_meaningful(ref)):
        tm = bytearray(vanilla)
        struct.pack_into('<I', tm, tex.O_COLORKEY, 1)
        template = bytes(tm)
    if name in NATIVE_SIZE_SHEETS and scale > 1 and 0 in imgs:
        imgs[0] = grow_frame_core(imgs[0], scale)
    if n_pal > 1 and not font:
        return joint_tex(template, imgs, scale)
    out, note = DT.convert_group(template, imgs, scale=scale,
                                 texture_name=name, smooth_downscale=True)
    parsed = tex.parse(out)
    if parsed and parsed['width'] != t['width']:
        sx = parsed['width'] // t['width']
        sy = parsed['height'] // t['height']
        out = tex.mark_logical_scale(out, sx, sy)
    return out, note


def build_textures(vanilla_archive, cache_dir, log=lambda *_: None):
    """{entry name: path of the built TEX} for every menu sheet ESUI's
    active folders replace and the Switch archive has."""
    import hashlib
    import lgp
    srcs = texture_sources(log)
    if not srcs:
        return {}
    arc = lgp.Archive(vanilla_archive)
    st = os.stat(CURRENT['mod'].path)
    os.makedirs(cache_dir, exist_ok=True)
    out = {}
    for name in sorted(srcs):
        e = arc.index.get(name)
        if e is None:
            continue
        scale = sheet_scale(name)
        vanilla = e['payload']
        key = hashlib.sha256(
            TEX_VERSION + vanilla + str((scale, st.st_size, st.st_mtime_ns,
                                         sorted(srcs[name].items())))
            .encode()).hexdigest()[:24]
        path = os.path.join(cache_dir, '%s.%s.tex' % (name[:-4], key))
        if not os.path.isfile(path):
            try:
                data, note = build_texture(name, vanilla, srcs[name], scale, log)
            except Exception as exc:                          # noqa: BLE001
                log('    ! %s: %s -- the game\'s own kept' % (name, exc))
                continue
            if data is None:
                log('    ! %s: %s' % (name, note))
                continue
            with open(path + '.tmp', 'wb') as f:
                f.write(data)
            os.replace(path + '.tmp', path)
        out[name] = path
    return out


# ---------------------------------------------------------------- coverage
# x86 extents of the functions the carried screens live in. A HEXT file from
# an active option folder that patches inside one of these, and is not one
# of the files this module carries, means a layout the build cannot match.
MAIN_FUNCTIONS = ((0x6C62A2, 0x6C66C8), (0x6C98A6, 0x6C9D07),
                  (0x6CA1EF, 0x6CA32D), (0x6CA346, 0x6CB56A),
                  (0x6F62F7, 0x6F638C), (0x6F9739, 0x6F9C44),
                  (0x70212A, 0x703344))
CARRIED = {('enhancedstock', '00-main.txt'), ('enhancedstock', '00-spacing.txt'),
           ('enhancedstock', '03-battle.txt'), ('fullmenu', 'all.txt'),
           ('echos/main', '05 - esui theme.txt')}


def uncovered(log=lambda *_: None):
    """[(folder, file, n)] of active HEXT that touch main-menu code but are
    not carried."""
    import exe_patch
    if CURRENT is None:
        return []
    root = CURRENT['mod'].cache
    bad = []
    for folder in CURRENT['folders']:
        for h in _hext_files(root, folder):
            key = (folder.lower(), os.path.basename(h).lower())
            if key in CARRIED:
                continue
            n = 0
            for va, b in exe_patch.parse_hext(open(h, encoding='utf-8',
                                                   errors='replace').read()):
                if any(s <= va < e for s, e in MAIN_FUNCTIONS):
                    n += 1
            if n:
                bad.append((folder, os.path.basename(h), n))
    return bad
