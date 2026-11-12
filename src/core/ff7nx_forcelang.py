"""ff7nx_forcelang.py -- BUILD 915: Settings > Languages > Force English.

HOW THE PORT PICKS ITS LANGUAGE (measured on exefs/main 1.0.3):

  * One global, [0x12E8320]: 0 English, 1 French, 2 German, 3 Spanish,
    4 Japanese (its name table at +0x12C9A50 and the "en" default at
    +0x11AD30B). Read through a one-line getter, +0x10FAD60, with 175
    callers: archive names, lang-XX folders, text, movies.
  * Written ONLY by the setter +0x10FADA0, called once at start-up
    (+0x10FD4C4). It asks the platform object (+0x111CA50 -> the virtual at
    +0x111CC10, which maps nn::oe::GetDesiredLanguage -- the CONSOLE's
    system language) and jumps through a 23-entry table. Every console
    language the game does not support lands on the English case,
    +0x10FAE24: `str wzr, [global]`, then the frame's epilogue.
  * +0x10E1AE8 then renames the game's archives (field/flevel.lgp ->
    field/sflevel.lgp, menu_us -> menu_sp, ...; 54 rows at +0x12980E0) when
    the global is 1..3.

So there is no in-game language setting: the console decides. Force English
replaces the setter's platform call with a branch to its English case. The
frame set up at +0x10FADA0 is the one that case tears down, so it returns
normally with the global at 0, and the whole game -- fields, menus, battle
and kernel text, world map, credits, minigames -- uses the English files the
build writes, with every mod applied.

One word, in the ordinary gated word-patch path (ff7nx_60fps NSO_GATED).

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import a64 as A

GROUP = 'force-english'
ENV = 'SEVENTH_NX_FORCE_ENGLISH'
SETTER = 0x10FADA0
SITE = 0x10FADB4                 # bl platform_language
PLATFORM_LANGUAGE = 0x111CA50
ENGLISH_CASE = 0x10FAE24         # str wzr, [0x12E8320]; ldp; ret
LANG_GLOBAL = 0x12E8320


def patch():
    return [('force English: the language is not taken from the console '
             '(the setter goes straight to its English case)',
             SITE, A.bl(SITE, PLATFORM_LANGUAGE), A.b(SITE, ENGLISH_CASE))]
