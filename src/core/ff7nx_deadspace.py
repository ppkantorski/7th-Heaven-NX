#!/usr/bin/env python3
"""
ff7nx_deadspace.py -- code space borrowed from a translated function that is
PROVEN dead, shared between the passes that need it.

The padding pool is spent by the late module passes (BUILD 532 measured it:
no 1 MB window has room for a windowed cave on a finished module). x86
0x623D28, the 25 KB PSX tile rasteriser, is reached only from 0x620BD3, which
nothing reaches -- `ff7nx_fieldbg._liveness` proves both on every build. Its
ARM body is 72 KB.

It is split so a shipping pass and the diagnostic probe can never collide:

    [lo + 0x40,    lo + 0x300)  'ship': ff7nx_campreserve (356 bytes; 2 KB
                                until BUILD 739)
    [lo + 0x300,   lo + 0x800)  'amb': ff7nx_ambient (BUILD 739) -- the
                                ambience fade and rotation runtime, which the
                                spent padding pool has no room for
    [lo + 0x800,   lo + 0x8C0)  'pace': ff7nx_fieldpace's late gates (two
                                caves, 84 bytes; 1 KB until BUILD 708,
                                768 B until BUILD 740, 256 B until 741)
    [lo + 0x8C0,   lo + 0xC00)  'sfxbat': ff7nx_sfxbattle (BUILD 740; 832 B
                                since 741's same-channel repeat fix) --
                                the per-character battle SFX routes, their
                                route records and variant lengths, which the
                                spent padding pool has no room for
    [lo + 0xC00,   lo + SHIP)   'speak': ff7nx_facial (BUILD 708) -- the lip
                                flap's speaker lookup (592 bytes), which the
                                spent padding pool no longer has room for
    [lo + 0x1400,  lo + 0x1800) 'fmt':  ff7nx_fmtcache (BUILD 813) -- audio.fmt
                                served from memory
    [lo + SHIP,    lo + 0x1400) 'sub':  ff7nx_sublines (BUILD 557) -- the
                                submarine limiter, line pen and depth pull;
                                the padding pool has no multi-word hole
                                left by the time that pass runs
    [lo + 0x1800,  lo + 0x1A00) 'coaster': ff7nx_coaster (BUILD 561) -- the
                                Gold Saucer coaster's analogue aim (1 KB
                                since BUILD 687, 512 B since 742; it uses
                                304 bytes)
    [lo + 0x1A00,  lo + 0x1C00) 'sfxtab': ff7nx_sfxbattle (BUILD 742) -- its
                                route records and variant lengths, read-only
                                data, so the cave part keeps its headroom
    [lo + 0x1C00,  lo + 0x2000) 'dntables': ff7nx_daynight (BUILD 687) -- the
                                day/night's four read-only tables (560 bytes)
                                when the contiguous tails are full. The
                                'daynight' part has 512 bytes left after its
                                caves, so it cannot hold them (log 396)
    [lo + 0x2000,  lo + 0x2400) 'condor': ff7nx_condor (BUILD 565) -- the
                                Fort Condor map-window cave
    [lo + 0x2400,  lo + 0x2600) 'zoom': ff7nx_fieldzoom (BUILD 567) -- the
                                per-field 4:3 zoom (convil_2's window view)
    [lo + 0x2600,  lo + 0x2A00) 'condorpad': ff7nx_condorpad (BUILD 569) --
                                Fort Condor's 360-degree, slower cursor
    [lo + 0x2A00,  lo + 0x2E00) 'canim': ff7nx_canim60 (BUILD 580) -- the
                                60 FPS partial-animation frame mapping
    [lo + 0x2E00,  lo + 0x3200) 'calendar': ff7nx_calendar (BUILD 596) --
                                the menu date row and clock caves, when the
                                padding pool has no room left for them (1 KB
                                since BUILD 693: they take 540 bytes)
    [lo + 0x3200,  lo + 0x3500) 'follow': ff7nx_subfollow (BUILD 693) -- the
                                player camera's float position (528 bytes)
    [lo + 0x3500,  lo + 0x3600) 'winrep': ff7nx_winrepeat (BUILD 694) -- the
                                window colour sliders' press-then-repeat
    [lo + 0x3600,  lo + 0x3C00) 'campos': ff7nx_campos (BUILD 610) -- the
                                field camera's unfloored follow position
    [lo + 0x3C00,  lo + 0x4C00) 'pointers': ff7nx_pointers (BUILD 611) --
                                the pointer hand and the exit-arrow table
    [lo + 0x4C00,  lo + 0x6000) 'daynight': ff7nx_daynight (BUILD 614b) --
                                the whole day/night runtime, when the
                                padding pool has no window left for it
                                (4 KB since BUILD 650's ultra-bright night,
                                5 KB since 661's minigame background split)
    [lo + 0x6000,  lo + 0x9800) 'shadow': ff7nx_shadows (BUILD 645; 4 KB
                                since 645h, 6 KB since 646i, 8 KB since 647,
                                10 KB since 647e, 9 KB in 650, 10 KB in 651, 12 KB since 655,
                                14 KB since 662, 12 KB since 665e, 14 KB
                                since 667d) -- model shadows
    [lo + 0x9800,  lo + 0xA000) 'wmtime': ff7nx_wmtime (BUILD 649) -- the
                                world map's night and sunset looks on the
                                day/night clock
    [lo + 0xA000,  lo + 0xA840) 'light': ff7nx_lighting (BUILD 675) --
                                FFNx's per-vertex model lighting. Was
                                'sfxpre' (ff7nx_sfxpreload, BUILD 665: opt-in,
                                and it never fitted -- it now gets an empty
                                part, logs and skips as before)
    [lo + 0xA840,  lo + 0xAEA0) 'subcam': ff7nx_subcam (BUILD 670) -- the
                                field camera's scripted moves in sub-unit
                                steps
    [lo + 0xAEA0,  lo + 0xB000) 'blank': ff7nx_blankhold (BUILD 665) -- a
                                frame with nothing drawn shows the last one
    [lo + 0xA000,  hi - FAR)    ff7nx_drawprobe, diagnostic builds only --
                                the SAME space as 'light' + 'subcam' +
                                'blank': a draw-probe build
                                (SEVENTH_NX_FRAME_PROBE=draw) leaves those out
                                (build.apply_lighting/_subcam/_blank_hold).
                                BUILD 786: the timing probe (=1) no longer
                                uses this part; it has a dead body of its
                                own (ff7nx_frameprobe.OWN_DEAD_X86)
    [hi - FAR,     hi)          'far': ff7nx_worldfar (BUILD 585) -- the
                                world map's far-field terrain pass, a
                                compiled C blob plus its hook stub

Each owner checks that ITS part is still the stock bytes before writing, so
a second writer to the same part is refused rather than silently merged.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import nxmap

DEAD_TRAMPOLINE = 0x620BD3
DEAD_BODY = 0x623D28
SHIP_BYTES = 0x1000
ENTRY_GUARD = 0x40
AMB_AT = 0x300                        # BUILD 739: the upper part of 'ship'
PACE_AT = 0x800
SFXBAT_AT = 0x8C0                     # BUILD 741: was 0x900 (740); 'pace' needs 84 B
SPEAK_AT = 0xC00                      # BUILD 708: the upper KB of 'pace'
SUB_AT = 0x1000
FMT_AT = 0x1400                       # BUILD 813: the upper KB of 'sub' (sublines
                                      # uses 748 of 2,048) -- ff7nx_fmtcache
COASTER_AT = 0x1800
SFXTAB_AT = 0x1A00                    # BUILD 742: the upper half of 'coaster'
DNTABLES_AT = 0x1C00                  # BUILD 687: the upper half of 'coaster'
CONDOR_AT = 0x2000
ZOOM_AT = 0x2400
PAD_AT = 0x2600
CANIM_AT = 0x2A00
CALENDAR_AT = 0x2E00
FOLLOW_AT = 0x3200                    # BUILD 693: the upper KB of 'calendar'
WINREP_AT = 0x3500                    # BUILD 694: the top of 'follow'
CAMPOS_AT = 0x3600
POINTERS_AT = 0x3C00
DAYNIGHT_AT = 0x4C00
SHADOW_AT = 0x6000                    # BUILD 661: was 0x5C00 (daynight +1 KB)
WMTIME_AT = 0x9800                    # BUILD 667d: was 0x9000 (665e); shadow 14 KB again (667d model mark)
SFXPRE_AT = 0xA000                    # BUILD 667d: was 0x9800 (665e). 3,840 bytes: too small for
                                      # the (now opt-in) sfx preload, which then logs and skips
LIGHT_AT = 0xA000                     # BUILD 675: ff7nx_lighting (the old 'sfxpre' part)
SUBCAM_AT = 0xA840                    # BUILD 783: was 0xA800. lighting's battle
                                      # test needs 2,104 of 'light'; subcam
                                      # uses 1,616 of the 1,728 left.
                                      # BUILD 670: the tail of 'sfxpre' (whose
                                      # opt-in preload did not fit 3,840 bytes
                                      # either -- it logs and skips)
BLANK_AT = 0xAEA0                     # BUILD 812: was 0xAF00 (665); blankhold's
                                      # handoff hold needs 312 B, subcam uses 1,616
                                      # of the 1,632 left below it
PROBE_AT = 0xB000                     # BUILD 665: end of 'blank' (the probe now
                                      # reuses SFXPRE_AT..far, see part())
FAR_BYTES = 0x6A00                     # BUILD 665: was 0x6C00 (662), 0x7400 (661), 0x7800 (655);
                                       # worldfar uses 27,048 of 27,136


def region(src, stock=None):
    """(lo, hi) of the dead ARM body in module `src`, proven dead there."""
    import ff7nx_fieldbg
    m = nxmap.Main(src)
    spans = []
    for va in (DEAD_TRAMPOLINE, DEAD_BODY):
        why = ff7nx_fieldbg._liveness(m, va, spans)
        if why:
            raise ValueError('x86 0x%X is not dead: %s' % (va, why))
        spans.append(m.extent(va))
    return m.extent(DEAD_BODY)


def part(src, which, stock=None):
    """(lo, hi) of one owner's part, refused if it was already written."""
    lo, hi = region(src)
    if which == 'ship':
        # never at the entry itself: the liveness proof above asks whether
        # any branch targets the dead function's ENTRY, and a cave placed
        # there would make the next build's proof fail on our own hook.
        p = (lo + ENTRY_GUARD, lo + AMB_AT)
    elif which == 'amb':
        p = (lo + AMB_AT, lo + PACE_AT)
    elif which == 'pace':
        p = (lo + PACE_AT, lo + SFXBAT_AT)
    elif which == 'sfxbat':
        p = (lo + SFXBAT_AT, lo + SPEAK_AT)
    elif which == 'speak':
        p = (lo + SPEAK_AT, lo + SHIP_BYTES)
    elif which == 'sub':
        p = (lo + SUB_AT, lo + FMT_AT)
    elif which == 'fmt':
        p = (lo + FMT_AT, lo + COASTER_AT)
    elif which == 'coaster':
        p = (lo + COASTER_AT, lo + SFXTAB_AT)
    elif which == 'sfxtab':
        p = (lo + SFXTAB_AT, lo + DNTABLES_AT)
    elif which == 'dntables':
        p = (lo + DNTABLES_AT, lo + CONDOR_AT)
    elif which == 'condor':
        p = (lo + CONDOR_AT, lo + ZOOM_AT)
    elif which == 'zoom':
        p = (lo + ZOOM_AT, lo + PAD_AT)
    elif which == 'condorpad':
        p = (lo + PAD_AT, lo + CANIM_AT)
    elif which == 'canim':
        p = (lo + CANIM_AT, lo + CALENDAR_AT)
    elif which == 'calendar':
        p = (lo + CALENDAR_AT, lo + FOLLOW_AT)
    elif which == 'follow':
        p = (lo + FOLLOW_AT, lo + WINREP_AT)
    elif which == 'winrep':
        p = (lo + WINREP_AT, lo + CAMPOS_AT)
    elif which == 'campos':
        p = (lo + CAMPOS_AT, lo + POINTERS_AT)
    elif which == 'pointers':
        p = (lo + POINTERS_AT, lo + DAYNIGHT_AT)
    elif which == 'daynight':
        p = (lo + DAYNIGHT_AT, lo + SHADOW_AT)
    elif which == 'shadow':
        p = (lo + SHADOW_AT, lo + WMTIME_AT)
    elif which == 'wmtime':
        p = (lo + WMTIME_AT, lo + SFXPRE_AT)
    elif which == 'sfxpre':
        # BUILD 675: its space is 'light' (it never fitted; it logs and skips)
        p = (lo + SFXPRE_AT, lo + SFXPRE_AT)
    elif which == 'light':
        p = (lo + LIGHT_AT, lo + SUBCAM_AT)
    elif which == 'subcam':
        p = (lo + SUBCAM_AT, lo + BLANK_AT)
    elif which == 'blank':
        p = (lo + BLANK_AT, lo + PROBE_AT)
    elif which == 'probe':
        # BUILD 665: shares the sfxpre/blank parts (a probe build skips them)
        p = (lo + SFXPRE_AT, (hi - FAR_BYTES) & ~15)
    elif which == 'far':
        p = ((hi - FAR_BYTES) & ~15, hi)
    else:
        raise ValueError(which)
    if stock is not None:
        a = nxmap.Main(src).text[p[0]:p[1]]
        b = nxmap.Main(stock).text[p[0]:p[1]]
        if a != b:
            raise ValueError('dead-space part %r at +0x%X was already '
                             'written by another pass' % (which, p[0]))
    return p


class Bump:
    """Contiguous allocator inside one part, 16-byte aligned caves."""

    def __init__(self, lo, hi):
        self.lo = lo
        self.at, self.hi = (lo + 15) & ~15, hi
        self.placed = {}

    def put(self, build):
        n = len(build(0, lambda i: 4 * i))
        entry = self.at
        words = build(entry, lambda i: entry + 4 * i)
        if len(words) != n or entry + 4 * n > self.hi:
            raise ValueError('cave does not fit in dead space')
        for i, w in enumerate(words):
            self.placed[entry + 4 * i] = w
        self.at = (entry + 4 * n + 15) & ~15
        return entry

    def put_bytes(self, data):
        """Read-only data, 16-byte aligned, returned address."""
        at = self.at
        data = bytes(data) + b'\0' * (-len(data) % 4)
        if at + len(data) > self.hi:
            raise ValueError('data does not fit in dead space')
        for i in range(0, len(data), 4):
            self.placed[at + i] = int.from_bytes(data[i:i + 4], 'little')
        self.at = (at + len(data) + 15) & ~15
        return at
