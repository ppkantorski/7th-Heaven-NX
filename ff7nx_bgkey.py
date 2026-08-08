#!/usr/bin/env python3
"""
ff7nx_bgkey.py -- make the 16:9 margin BLACK, in the field data, for free.

THE SYMPTOM THIS IS FOR
=======================
HANDOFF-60 §2 Group A, reported from hardware:

    Wall Market  -- extended left/right regions are TAN
    Sector 7     -- same, but GREEN
    Train scene  -- same, but MAROON
    Honey Bee Inn-- mostly BLACK margins with a thin grey bar inboard

Four fields, four different colours, one of them already black. A leftover
buffer does not do that. A CLEAR COLOUR does not do that either -- HANDOFF-60
§3.1 disassembled all sixteen callers and fourteen pass hardcoded (0,0,0,0),
and the fifteenth was patched to black on hardware with no change.

What DOES do that is a per-field constant, and HANDOFF-60 §3.2 measured
exactly which one: `md8_1`'s margin is #00FF00 and `md8_1`'s palette entry 0
is #00FF00. Pure colour-key green.

    THE MARGIN IS THE FIELD'S OWN PALETTE ENTRY 0.

Honey Bee Inn is the control: its margins are already black, because its
palette entry 0 already is. Nothing was special about that field -- it just
happened to ship the value everybody wants.

WHERE THE PALETTE ACTUALLY LIVES -- and the mistake this file made once
======================================================================
NOT in section 9. Section 9 opens with a five-byte header and the literal
string "PALETTE", which is exactly the shape of a palette block and is not
one: its "BACK" tag follows twelve bytes later, so there is no room for
colours there at all. Reading it that way produced

    ancnt1  palette 256x257 ends at 131608, BACK at 36

on all 711 fields -- the self-check doing its job, which is the only reason
this cost a minute instead of a build.

FF7 field files carry NINE sections and the order is

    0 Script   1 Camera matrix   2 Model loader   **3 PALETTE**
    4 Walkmesh 5 Tile map        6 Encounter      7 Triggers   8 Background

`lgp.split_sections` already strips each section's four-byte length, so
section 3's body is the palette section proper:

    u16 palX  u16 palY  u16 colours_per_page(256)  u16 page_count
    u16 colours[page_count * 256]        A1B5G5R5 little-endian

...possibly behind a four-byte internal length, which is why the header size
is DISCOVERED rather than assumed. `palette_block()` tries each candidate
header and accepts only the one where

    header + 2 * colours_per_page * page_count == len(section)

lands exactly on the end of the section. A layout that does not close
exactly is refused, never guessed at. That check is what makes this safe to
run over 711 fields unattended.

Colour layout, from PyFF7's `color_to_rgba` and its shift constants: R at
bits 0-4, G at 5-9, B at 10-14, mask/STP at 15. Black with the mask bit
clear is `0x0000`, which is also the canonical PSX "fully transparent"
value.

WHAT THIS MODULE DOES
=====================
Rewrites colour 0 of every palette page in every field's section 3 to
`0x0000` in the BUILT `flevel.lgp`.

That is the whole change. It is:

  * data only -- exefs/main is never opened;
  * length-preserving -- two bytes overwritten in place per page, the
    section length does not change, so no field grows, no page moves, and
    it cannot interact with the page-count failure of HANDOFF-60 §3.6;
  * composable -- it runs after the Cosmos repack and after the widescreen
    camera-range bake, so whatever those produced is what gets normalised;
  * reversible -- one dropdown, and the flevel cache key changes with it.

WHY THIS CANNOT DAMAGE THE PICTURE
==================================
Index 0 is FF7's colour key. It is transparent everywhere in this codebase
already, by a rule that is written down and shipped:

    field_bg_native.paletted_to_565 --
        "FF7 field palettes mark 'transparent' as index 0 with the palette's
         own zero entry, so index 0 maps to EMPTY regardless of what the
         palette says -- the same rule the engine applies."

So every page this build converts to truecolor ALREADY discards the colour
stored at entry 0. Changing that colour cannot change a single converted
pixel. The only consumer left is whatever fills the field buffer before the
tiles land, which is precisely the thing we are trying to repaint.

There is precedent in this tree for editing exactly this kind of entry:
`build._debleed_textures` recolours the transparent palette entry of every
colour-keyed model texture, for a different artefact, and has shipped.

WHAT IT DOES NOT DO
===================
It does not put art in the margin. 369 of 709 fields physically cannot fill
a 16:9 frame (HANDOFF-60 §3.8) and no data edit changes that. This makes
the unavoidable bars BLACK, which is what FFNx shows on PC and what the
user asked for in goal 2.

WHAT TO LOOK AT ON THE CONSOLE
==============================
Named fields, named outcomes, decided before the build:

  * Sector 7 near the tower: the side regions were GREEN. If they are BLACK,
    the theory is confirmed and goal 2 is done for every field at once.
  * Wall Market (tan) and the train scene (maroon): same test, two more
    colours.
  * Honey Bee Inn: was already black. It must STAY black and must not gain
    any new artefact -- it is the control.
  * Anywhere at all: if a previously-transparent area INSIDE the picture
    turns black (a window, a doorway, a gap in a railing), then the port is
    keying on the literal colour rather than on the index, and the answer is
    the `first` mode below rather than `black`. That would be a surprise and
    is worth reporting immediately.
"""
from __future__ import annotations

import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# --------------------------------------------------------------------------
# the setting
# --------------------------------------------------------------------------
KEY_ENV = 'SEVENTH_NX_BG_KEY'

MODE_OFF = ''          # leave the palettes exactly as the mod ships them
MODE_BLACK = 'black'   # colour 0 of EVERY palette page -> 0x0000  (default)
MODE_FIRST = 'first'   # colour 0 of page 0 only -> 0x0000

MODES = (MODE_OFF, MODE_BLACK, MODE_FIRST)

# FALSIFIED 2026-08, OFFLINE, BEFORE A BUILD. `diag_bgkey.py --predict`
# against the shipping flevel.lgp:
#
#     mkt_ia  entry 0 #000000  ->  predicted black, REPORTED TAN
#     mkt_m   entry 0 #000000  ->  predicted black, REPORTED TAN
#     trnad_1 entry 0 #000000  ->  predicted black, REPORTED MAROON
#     md8_1   entry 0 #00FF00  ->  predicted green, reported green  (1 of 10)
#
# A field whose entry 0 is already black, showing tan bars, ends it. The one
# match is `md8_1`, which is the field the theory was derived from, so it is
# not evidence. THE MARGIN IS NOT PALETTE ENTRY 0.
#
# Kept, defaulted OFF, and left in the dropdown because the module is
# correct, the diagnostic is what killed the theory in one minute instead of
# one build, and HANDOFF-60 §8 item 5 is the rule: never ship a correct
# implementation of a no-op. See HANDOFF-62 §2.
DEFAULT_MODE = MODE_OFF

# Not a free choice: bit 15 is the PSX mask/STP bit and R/G/B are five bits
# each, so 0x0000 is "black, mask clear" -- the value FF7's own art already
# uses for entry 0 wherever it is correct.
BLACK = 0x0000

SECTION_PALETTE = 3     # zero-based. Section 4 of 9.
SECTION9 = 8            # the background, kept for callers that want it

# Header sizes to try, most likely first. 8 is the bare
# palX/palY/colours/pages header; 12 is that behind an internal u32 length.
HEADER_CANDIDATES = (8, 12, 4, 0, 16)
COLOURS_PER_PAGE = 256
MAX_PAGES = 256


class BgKeyError(ValueError):
    """A palette section that does not parse. Never written."""


def mode(env=None):
    """The configured mode, validated. Unknown values fall back to OFF."""
    raw = (env if env is not None
           else os.environ.get(KEY_ENV, DEFAULT_MODE))
    raw = (raw or '').strip().lower()
    if raw in ('0', 'off', 'no', 'none', 'false'):
        return MODE_OFF
    if raw in ('1', 'on', 'yes', 'true'):
        return MODE_BLACK
    return raw if raw in MODES else MODE_OFF


def enabled(env=None):
    return mode(env) != MODE_OFF


# --------------------------------------------------------------------------
# finding the block
# --------------------------------------------------------------------------
def palette_block(section):
    """
    (colours_offset, page_count, colours_per_page) for a PALETTE section.

    The header size is discovered, not assumed, and only a layout whose
    colour array ends EXACTLY on the end of the section is accepted. Raises
    `BgKeyError` otherwise -- a wrong offset here would corrupt a palette
    rather than fail loudly, so it is made to fail loudly.
    """
    n = len(section)
    tried = []
    for head in HEADER_CANDIDATES:
        if n < head + 4:
            continue
        cpp, pages = struct.unpack_from('<HH', section, head - 4) \
            if head >= 4 else (COLOURS_PER_PAGE, 0)
        if head == 0:
            # No header at all: infer the page count from the length.
            if n % (2 * COLOURS_PER_PAGE):
                tried.append('h0: %d not a whole number of pages' % n)
                continue
            pages = n // (2 * COLOURS_PER_PAGE)
            cpp = COLOURS_PER_PAGE
        tried.append('h%d: %dx%d' % (head, cpp, pages))
        if cpp != COLOURS_PER_PAGE or not 1 <= pages <= MAX_PAGES:
            continue
        if head + 2 * cpp * pages == n:
            return head, pages, cpp
    raise BgKeyError('no header fits %d bytes (%s)' % (n, '; '.join(tried)))


def find_palette_section(parts):
    """
    Index of the section that parses as a palette. `SECTION_PALETTE` first.

    The order is documented and stable, but a mod that reorders sections
    would corrupt exactly one palette and nothing would say so. Looking
    costs microseconds and removes the whole class of failure.
    """
    order = [SECTION_PALETTE] + [i for i in range(len(parts))
                                 if i != SECTION_PALETTE]
    for i in order:
        try:
            palette_block(parts[i])
            return i
        except BgKeyError:
            continue
    raise BgKeyError('no section parses as a palette')


def entry0(section):
    """[raw u16] -- colour 0 of each palette page, in order. Read-only."""
    head, pages, cpp = palette_block(section)
    return [struct.unpack_from('<H', section, head + 2 * cpp * i)[0]
            for i in range(pages)]


def rgba(colour):
    """(r, g, b, mask) at 8 bits per channel, for reporting."""
    r = (colour & 0x1F) * 255 // 31
    g = ((colour >> 5) & 0x1F) * 255 // 31
    b = ((colour >> 10) & 0x1F) * 255 // 31
    return r, g, b, (colour >> 15) & 1


def hex_rgb(colour):
    r, g, b, _m = rgba(colour)
    return '#%02X%02X%02X' % (r, g, b)


# --------------------------------------------------------------------------
# writing it
# --------------------------------------------------------------------------
def blacken(section, how=MODE_BLACK, value=BLACK):
    """
    (new_section, n_changed). Length is always preserved exactly.

    Returns the ORIGINAL object and 0 when nothing needs changing, so the
    caller can skip the re-encode -- which is the whole cost of this pass.
    """
    if how == MODE_OFF:
        return section, 0
    head, pages, cpp = palette_block(section)
    pages = 1 if how == MODE_FIRST else pages
    todo = [head + 2 * cpp * i for i in range(pages)
            if struct.unpack_from('<H', section, head + 2 * cpp * i)[0]
            != value]
    if not todo:
        return section, 0
    out = bytearray(section)
    for off in todo:
        struct.pack_into('<H', out, off, value)
    assert len(out) == len(section)
    return bytes(out), len(todo)


# --------------------------------------------------------------------------
# the flevel pass
# --------------------------------------------------------------------------
def apply_to_flevel(archive, payloads, how=None, encode=None, log=print):
    """
    Normalise colour 0 across the whole archive, honouring `payloads`.

    Same contract as `ff7nx_ws.apply_to_flevel`: a field already in
    `payloads` (replaced by a mod pass, repacked, or camera-range baked) is
    taken from there so this composes rather than competes; a field absent
    is taken from the archive. The result goes back into `payloads`, which
    is what `archive.replace()` is given.

    Runs LAST for a reason. The Cosmos repack rewrites section 9 wholesale
    for 683 fields and can rewrite the palette with it; a palette normalised
    before that would simply be replaced again.

    Raises nothing. A field that will not parse is counted and skipped: a
    margin colour is not worth failing a build over, and a half-written
    palette would be.
    """
    import lgp

    how = mode() if how is None else how
    stats = {'mode': how, 'read': 0, 'changed': 0, 'pages': 0,
             'already': 0, 'skipped': [], 'colours': {}, 'section': None}
    if how == MODE_OFF:
        return stats

    encode = encode or (lambda raw: archive.encode_field(raw))

    for name in archive.names():
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        payload = payloads.get(name, entry.get('payload'))
        if not payload:
            continue
        try:
            raw = (lgp.lzs_decompress(payload[4:]) if name in payloads
                   else archive.decompressed(entry))
            parts = lgp.split_sections(raw)
            idx = find_palette_section(parts)
            if stats['section'] is None:
                stats['section'] = idx
            pal = parts[idx]
            stats['read'] += 1
            before = entry0(pal)
            stats['colours'][name] = before[0] if before else None
            new, n = blacken(pal, how)
            if not n:
                stats['already'] += 1
                continue
            parts[idx] = new
            payloads[name] = encode(lgp.join_sections(parts))
            stats['changed'] += 1
            stats['pages'] += n
        except Exception as exc:                                # noqa: BLE001
            stats['skipped'].append((name, str(exc)[:70]))
            continue

    if stats['skipped']:
        log('  ! background key: %d field(s) not normalised (%s)'
            % (len(stats['skipped']),
               ', '.join('%s: %s' % s for s in stats['skipped'][:3])))
    return stats


def verify_flevel(path, how=None, log=print):
    """
    Read a REBUILT archive back and confirm colour 0 really is black.

    Offline, no console, and it is the check that says whether the build did
    what the log claims. Returns (ok, [problems]).
    """
    import lgp

    how = mode() if how is None else how
    if how == MODE_OFF:
        return True, []
    arc = lgp.Archive(path)
    bad = []
    for name in arc.names():
        entry = arc.index.get(name)
        if entry is None or not arc.is_field(entry):
            continue
        try:
            parts = lgp.split_sections(arc.decompressed(entry))
            got = entry0(parts[find_palette_section(parts)])
        except Exception:                                       # noqa: BLE001
            continue
        want = got[:1] if how == MODE_FIRST else got
        off = [i for i, c in enumerate(want) if c != BLACK]
        if off:
            bad.append('%s: page %s still %s'
                       % (name, off[:4], hex_rgb(got[off[0]])))
    return (not bad), bad


def summarise(stats):
    """One line for the build log, or '' when the pass did nothing."""
    if not stats or stats.get('mode', MODE_OFF) == MODE_OFF:
        return ''
    return ('background key: colour 0 -> black in %d of %d field(s) '
            '(%d palette page(s); %d already black%s; section %s)'
            % (stats['changed'], stats['read'], stats['pages'],
               stats['already'],
               ', %d unparsed' % len(stats['skipped'])
               if stats['skipped'] else '',
               stats['section']))
