#!/usr/bin/env python3
"""
ff7nx_marginblack.py -- make the 16:9 margin BLACK by REPOINTING the filler
tiles, not by recolouring anything shared.

HANDOFF-65 §1 is the diagnosis this implements: the coloured bars are a flat
filler tile IN THE ARCHIVE, drawn correctly, in the colour the file holds.
HANDOFF-65 §4 proposed the fix as "write NEAR_BLACK into that palette index".

THAT PROPOSAL DOES NOT WORK, AND IT WAS MEASURED BEFORE IT WAS BUILT
====================================================================
`diag_marginfill.py`, over all 711 fields of the shipping flevel.lgp:

    flat margin filler tiles                       35,366  (depth 1)
    ... whose (page, palette, index) is ALSO
        sampled by a tile inside the 4:3 picture   15,093  (43%)

Per field, the same thing:

    bwhlin    128 of 128 margin tiles REFUSED -- index 1 on page 0 is art
    mds6_3     81 of 120 REFUSED
    mds7st3    31 of 120 REFUSED
    mrkt4      70 of  70 REFUSED

Writing the palette entry would therefore turn 43% of the bars black and
leave the rest, i.e. replace a uniform bar with a two-tone one, and on
`bwhlin` -- one of the three fields §4.1 names as the test -- it would change
nothing at all while blacking out art inside the picture. §4's refusal check
does not rescue it: the check is what says the edit is unavailable.

A FREE PALETTE INDEX IS NOT AVAILABLE EITHER
--------------------------------------------
The obvious repair -- pick an index nothing draws, point the filler at it,
and colour it black -- was implemented and measured before it was built. On
`mds7st3` and `bwhlin`, two of the three fields HANDOFF-65 §4.1 names as the
test, all 256 indices are drawn somewhere on some depth-1 page, so there is
no index whose colour can be changed without changing art. Recorded here so
it is not tried a fourth time.

WHAT THIS MODULE DOES INSTEAD
=============================
It gives the filler tiles a PALETTE PAGE OF THEIR OWN. Nothing that already
exists is modified at all.

  1. every entry of one palette page is NEAR_BLACK_555. That page is a
     SPARE one where the field has an unreferenced page (147 of 695 fields),
     and appended to section 3 otherwise -- +512 bytes, and the page count in
     the header goes up by one, which is the only thing in the field that
     changes size;
  2. every flat layer-1 margin tile gets `palette_ID` = that page.

The tile is FLAT, so it samples exactly one index; every index on the new
page is near-black; so the tile draws near-black whichever index it holds.
No pixel of any texture page is written, no existing palette entry is
written, and no other tile's record is touched. The 4:3 interior of every
field and the 466 fields with real margin art are unchanged by construction,
not by a threshold -- and `render_margin` re-renders and checks it anyway.

PER-TILE PALETTES ARE HONOURED, AND THAT WAS CHECKED
----------------------------------------------------
`field_bg_repack.py`'s note that "below 0x0F the engine creates ONE texture
for the page and the palette_ID byte selects nothing" describes FFNx's
external-texture path, where a page is dumped once. It is not true of the
native paletted draw, and the archive proves it: `nmkin_1` draws page 0 with
palette IDs 1 and 2 and page 1 with 2, 3 and 4, in the picture, in vanilla.
If the ID selected nothing that field would be miscoloured, and it is not.
HANDOFF-65 §1.2's renderer, which reproduced two screenshots pixel for
pixel, resolves every tile through its own `palette_ID` as well.

DEPTH-2 FILLER
--------------
1,136 flat margin tiles in 10 fields sit on truecolor pages, where there is
no palette to repoint. Those are counted and left alone: `stats['depth2']`.
They are 3% of the filler and none of them is in a field HANDOFF-65 names.

WHICH TILES
-----------
Layer 1 only, and only tiles whose 16x16 destination lies WHOLLY outside the
4:3 picture (dst_x + 16 <= -160 or dst_x >= 160). A tile straddling the
boundary has half of itself inside the frame and is never touched. Layers
2/3/4 are overlays and parallax and are left alone entirely.

A margin tile is filler only if its 16x16 source block is a SINGLE value. A
margin tile carrying real art is the widescreen extension working, and it is
what 466 fields have.

NEAR_BLACK_555 = 0x0400
-----------------------
Section-3 palette colours are A1B5G5R5: R bits 0-4, G 5-9, B 10-14, mask 15.
0x0400 is blue 1/31 -- RGB(0, 0, 8) -- the dimmest non-zero colour the format
has, and the reason it is not 0x0000 is `field_bg_native.NEAR_BLACK`'s: 0 is
this pipeline's transparency key, a transparent background pixel writes no
occlusion, and field models then draw in FRONT of scenery they should be
behind.

It is also, exactly, what `mrkt4` already ships -- HANDOFF-65 §1.3 measured
its margin filler at `#000008` and §1.5 lists Wall Market as the field the
user describes as correct. This module gives every other field the value the
one correct field already has.

Promotion to truecolor keeps it: `field_bg_native.rgb_to_565(0, 0, 8)`
quantises to the depth-2 `NEAR_BLACK` (now 0x0841, RGB(8,8,8)), so the
Cosmos repack running after this pass preserves the colour either way.

ORDERING
--------
Runs AFTER the field-background repack, and the reason is the repack's own
unit of work: it builds one truecolor texture per (page, cell, PALETTE) that
is actually referenced. A palette page introduced before it runs would be a
palette the mod has no image for, the nearest one would be borrowed for it
(`Stats.cells_borrowed`), and the filler would come back in its original
colour with the repack's name on it. Running afterwards, the tiles that are
still paletted are exactly the ones this pass can fix, and the ones the
repack promoted to truecolor are reported as `depth2` rather than silently
missed.

It runs BEFORE the widescreen camera-range bake for no deeper reason than
that bake owning section 8 last; this pass touches 3 and 9.
"""
from __future__ import annotations

import os
import struct
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import numpy as np

import diag_common as DC
import field_bg_native as FN

# ---------------------------------------------------------------- the setting
MARGIN_ENV = 'SEVENTH_NX_MARGIN_BLACK'
# OFF unless settings.json asks for it. This pass recolours the 16:9 filler
# and does nothing for the missing textures, which is the actual defect; with
# it on, 232 fields change and no observation about the textures is clean.
# `margin_black: 1` in settings.json turns it back on.
DEFAULT_ON = False

# A1B5G5R5. R 0-4, G 5-9, B 10-14, mask 15. Blue 1/31 = RGB(0, 0, 8).
NEAR_BLACK_555 = 0x0400

TILE = 16
HALF_43 = 160                    # the 4:3 picture is dst_x in [-160, 160)
PAGE_PX = 256                    # depth-1 pages are always 256
CELLS = PAGE_PX // TILE          # 16 x 16 cells per page

# tile record byte offsets, from PyFF7/PyFF7/field.py's read order
T_DSTX, T_DSTY = 2, 4
T_SRCX, T_SRCY = 10, 12
T_SRCX2, T_SRCY2 = 14, 16
T_PAL = 22
T_TEX, T_TEX2 = 32, 34
T_DEPTH = 36
T_SRCX_BIG, T_SRCY_BIG = 42, 46
BIG_FULL = 10000000              # src_x_big for a whole 256px page

SECTION_PALETTE = 3
SECTION9 = 8

# slot < 0x0F is the depth-1 opaque blend group -- field_bg_native.D1_GROUPS.
OPAQUE_D1_MAX = 0x0F


class MarginError(ValueError):
    """A field this pass will not touch. Always counted, never guessed at."""


def enabled(env=None):
    raw = (env if env is not None
           else os.environ.get(MARGIN_ENV, '1' if DEFAULT_ON else '0'))
    return str(raw).strip().lower() not in ('0', 'off', 'no', 'none', 'false',
                                            '')


# ------------------------------------------------------------------ palette
def palette_block(sec):
    """
    (header_size, n_pages, colours_per_page). The header size is DISCOVERED
    and only a layout whose colour array ends exactly on the end of the
    section is accepted -- ff7nx_bgkey.palette_block's rule, and the reason
    this is safe to run unattended over 711 fields.

    ZERO PAGES IS A VALID PALETTE, NOT A BROKEN ONE.

    A field whose background is entirely truecolor has no palette to carry, and
    ships a 12-byte section 3 that is header-only: `cpp = 256, npg = 0`.
    Requiring `npg >= 1` rejected those and the caller reported them as
    corruption. MEASURED on the build log this was written against:

        ! margin art: 16 field(s) not changed (kuro_11: MarginError: palette
          does not close, trnad_52: ..., gldst: ...)

    All 16 -- blin67_4, rckt32, nivgate3, gldinfo, nivgate2, junone22, hyoumap,
    trnad_52, gaiin_6, nivl_e3, rckt3, kuro_11, gldst, jtemplc and two more --
    hold ONLY depth-2 pages at slots 26+ and not one depth-1 page. There was
    never anything for a paletted pass to do in them. `npg = 0` now parses,
    `fillable_cells` finds no depth-1 cell, and the field is skipped silently
    instead of being reported as a failure.

    `cpp >= 1` still holds, so the 8-byte reading of these sections
    (`cpp = 0, npg = 480`) is still refused and the layout stays unambiguous.
    """
    for hdr in (8, 12, 16):
        if len(sec) < hdr:
            continue
        if hdr == 8:
            _x, _y, cpp, npg = struct.unpack_from('<HHHH', sec, 0)
        elif hdr == 12:
            _l, _x, _y, cpp, npg = struct.unpack_from('<IHHHH', sec, 0)
        else:
            _a, _l, _x, _y, cpp, npg = struct.unpack_from('<IIHHHH', sec, 0)
        if 1 <= cpp <= 1024 and 0 <= npg <= 256 \
                and hdr + 2 * cpp * npg == len(sec):
            return hdr, npg, cpp
    raise MarginError('palette does not close')


def rgb555(v):
    """A1B5G5R5 -> '#RRGGBB', for the build log."""
    return '#%02X%02X%02X' % ((v & 0x1F) * 255 // 31,
                              ((v >> 5) & 0x1F) * 255 // 31,
                              ((v >> 10) & 0x1F) * 255 // 31)


def palette_colours(sec):
    hdr, npg, cpp = palette_block(sec)
    return np.frombuffer(sec, '<u2', count=cpp * npg,
                         offset=hdr).reshape(npg, cpp), hdr, npg, cpp


# -------------------------------------------------------------------- tiles
class Tile:
    __slots__ = ('off', 'layer', 'dx', 'dy', 'slot', 'sx', 'sy', 'pal',
                 'depth', 'flat_value')

    def __init__(self, off, layer, dx, dy, slot, sx, sy, pal, depth):
        self.off = off
        self.layer = layer
        self.dx = dx
        self.dy = dy
        self.slot = slot
        self.sx = sx
        self.sy = sy
        self.pal = pal
        self.depth = depth
        self.flat_value = None

    @property
    def cell(self):
        return (self.slot, self.sx, self.sy)

    @property
    def outside_43(self):
        """Wholly outside the 4:3 picture, on ANY layer."""
        return self.dx + TILE <= -HALF_43 or self.dx >= HALF_43

    @property
    def is_margin(self):
        return self.layer == 1 and self.outside_43


def read_tiles(sec9, surv, pages):
    tiles = []
    for layer, offs in DC.walk_layers(sec9, surv['back_start'],
                                      surv['tex_start']):
        for o in offs:
            slot = sec9[o + T_TEX]
            p = pages.get(slot)
            tiles.append(Tile(
                o, layer,
                struct.unpack_from('<h', sec9, o + T_DSTX)[0],
                struct.unpack_from('<h', sec9, o + T_DSTY)[0],
                slot, sec9[o + T_SRCX], sec9[o + T_SRCY], sec9[o + T_PAL],
                p.depth if p is not None else 0))
    return tiles


def page_array(p):
    """A page as [y][x] in its native source unit, and the page/256 scale."""
    if p.depth == 1:
        return np.frombuffer(p.data, np.uint8).reshape(PAGE_PX, PAGE_PX), 1
    return np.frombuffer(p.data, '<u2').reshape(p.px, p.px), p.px // PAGE_PX


def source_block(arr, k, sx, sy):
    b = arr[sy * k:sy * k + TILE * k, sx * k:sx * k + TILE * k]
    return b if b.shape == (TILE * k, TILE * k) else None


# ------------------------------------------------------------------- the plan
class Plan:
    """What this pass decided for one field. Reported whether or not applied."""

    __slots__ = ('name', 'tiles', 'pal_page', 'appended', 'refusal',
                 'n_margin', 'n_flat', 'n_art', 'n_depth2', 'n_transparent',
                 'colour', 'n_stray')

    def __init__(self, name):
        self.name = name
        self.tiles = []          # flat depth-1 margin tiles to repoint
        self.pal_page = None     # the all-near-black palette page
        self.appended = False    # True if section 3 had to grow by one page
        self.refusal = None
        self.n_margin = self.n_flat = self.n_art = 0
        self.n_depth2 = self.n_transparent = 0
        self.n_stray = 0         # tiles that already carried the appended ID
        self.colour = None       # what the bar looks like NOW, for the log

    @property
    def ok(self):
        return self.refusal is None and bool(self.tiles)

    def line(self):
        if self.refusal:
            return '%-12s REFUSED  %s' % (self.name, self.refusal)
        if not self.tiles:
            return ('%-12s -        nothing to do (%d margin tile(s), %d art, '
                    '%d truecolor, %d transparent)'
                    % (self.name, self.n_margin, self.n_art, self.n_depth2,
                       self.n_transparent))
        return ('%-12s %4d filler tile(s) %s -> palette page %d (%s)%s%s'
                % (self.name, len(self.tiles), self.colour or '', self.pal_page,
                   'appended' if self.appended else 'spare',
                   ', %d truecolor left' % self.n_depth2
                   if self.n_depth2 else '',
                   ', %d stray margin tile(s) adopted' % self.n_stray
                   if self.n_stray else ''))


def plan_field(name, raw, lgp_mod):
    """
    Decide, without writing anything. Raises nothing: a field that cannot be
    done gets `plan.refusal` set and is counted.
    """
    plan = Plan(name)
    parts = lgp_mod.split_sections(raw)
    try:
        cols, hdr, npg, cpp = palette_colours(parts[SECTION_PALETTE])
    except MarginError as exc:
        plan.refusal = str(exc)
        return plan, parts, None
    sec9 = parts[SECTION9]
    try:
        surv = DC.survey(sec9)
    except Exception as exc:                                    # noqa: BLE001
        plan.refusal = 'section 9: %s' % str(exc)[:60]
        return plan, parts, None

    pages = {p.slot: p for p in surv['pages']}
    tiles = read_tiles(sec9, surv, pages)
    arrays = {slot: page_array(p) for slot, p in pages.items()}

    # Classify. A margin tile is filler only if its source block is flat.
    filler = []
    pal_used = set()
    for t in tiles:
        if t.depth == 1:
            pal_used.add(t.pal)
        a = arrays.get(t.slot)
        if a is None:
            continue
        b = source_block(a[0], a[1], t.sx, t.sy)
        if b is None or not t.is_margin:
            continue
        plan.n_margin += 1
        u = np.unique(b)
        if u.size != 1:
            plan.n_art += 1
            continue
        t.flat_value = int(u[0])
        if t.depth != 1:
            plan.n_depth2 += 1                 # truecolor: no palette to move
            continue
        if t.flat_value == 0:
            plan.n_transparent += 1            # already the colour key
            continue
        if t.slot >= OPAQUE_D1_MAX:
            # Blend groups: slot >= 0x0F is additive or averaged, where a
            # black filler is already invisible. Moving it would change how
            # it composites, so it is not moved.
            continue
        filler.append(t)
    plan.n_flat = len(filler)
    if not filler:
        return plan, parts, surv

    # What the bar looks like now, for the build log -- one line that ties a
    # field name to the colour on screen, which is what HANDOFF-65 §7 says
    # was missing for four handoffs.
    t0 = filler[0]
    if t0.pal < npg:
        plan.colour = rgb555(int(cols[t0.pal][t0.flat_value]))

    # IDEMPOTENCE, and it was measured rather than assumed. Without this,
    # running the pass twice over the same archive appends a SECOND all-black
    # page, repoints the filler at it and orphans the first -- +512 bytes and
    # one dead page per run, forever. Inside a build that never happens: the
    # build starts from the dump every time and each field is seen once. But
    # the module is also a standalone tool and its own output is a valid
    # input, so a page that is ALREADY entirely NEAR_BLACK is reused.
    #
    # This is a strict subset of what the pass would otherwise create, so it
    # cannot pick up a page that art depends on: a page every one of whose
    # entries is NEAR_BLACK_555 can only draw NEAR_BLACK.
    already = [i for i in range(npg) if bool((cols[i] == NEAR_BLACK_555).all())]
    if already:
        plan.pal_page = already[0]
        plan.appended = False
        plan.tiles = filler
        return plan, parts, surv

    # A palette page of their own: a spare one if the field has one, else a
    # new one. Growing section 3 is the only size change this pass makes and
    # `palette_block` re-derives the page count from the header, so nothing
    # downstream has to be told.
    spare = [i for i in range(npg) if i not in pal_used]
    if not spare and max(pal_used) >= npg:
        # A tile already carries a palette_ID past the end of the section --
        # `las4_42` does, and 53 fields do. Appending gives ID `npg` a meaning
        # it does not have today, and the meaning it gets is this pass's own
        # black page. THE BLAST RADIUS IS EXACTLY the tiles carrying `npg`,
        # and it was measured over all 711 fields before this branch was
        # written (HANDOFF-67 §2):
        #
        #     53 fields refused for overshoot
        #     46 of them have EVERY stray tile wholly OUTSIDE the 4:3 picture
        #
        # A stray tile in the margin drawing near-black is the pass's own
        # goal, not a hazard, so those 46 are allowed. A stray tile INSIDE
        # the picture would be a new black square in the frame -- the very
        # defect being hunted -- so a field with one is still refused.
        #
        # Only ID == npg is at stake. A tile carrying npg+2 stays undefined
        # after a one-page append, exactly as it is today.
        # A stray tile may be adopted only if it passes BOTH of this module's
        # tests, the same two every other tile has to pass:
        #
        #   POSITION -- wholly outside the 4:3 picture. Inside, it would be a
        #               new black square in the frame.
        #   FLATNESS -- a single source index. A stray tile with a MULTI-INDEX
        #               source block is real widescreen margin ART, and
        #               painting it near-black erases it.
        #
        # The first draft of this branch tested position and not flatness, and
        # it was wrong: measured over the archive, 2,414 of the 4,815 tiles it
        # would have adopted carry real art -- 480 in `las4_0`, 432 in
        # `sininb34`, 184 in `mds7plr1`. That is the widescreen extension
        # working, which is the one thing this pass exists to preserve.
        # HANDOFF-67 §2.2 is corrected by HANDOFF-68 §1.
        bad = []
        n_stray = 0
        for t in tiles:
            if t.depth != 1 or t.pal != npg:
                continue
            n_stray += 1
            if not t.outside_43:
                bad.append('inside the picture')
                continue
            a = arrays.get(t.slot)
            b = None if a is None else source_block(a[0], a[1], t.sx, t.sy)
            if b is None or np.unique(b).size != 1:
                bad.append('carrying art')
        if bad:
            plan.refusal = ('%d of %d tile(s) carrying palette_ID %d are %s; '
                            'appending would redefine them'
                            % (len(bad), n_stray, npg,
                               ' / '.join(sorted(set(bad)))))
            return plan, parts, surv
        plan.n_stray = n_stray
    plan.pal_page = spare[0] if spare else npg
    plan.appended = not spare
    plan.tiles = filler
    return plan, parts, surv


def apply_plan(plan, parts, surv):
    """Write the plan into `parts`. Returns the new section list."""
    if not plan.ok:
        return None

    # 1. the palette page: every entry NEAR_BLACK.
    pal = bytearray(parts[SECTION_PALETTE])
    hdr, npg, cpp = palette_block(bytes(pal))
    if plan.appended:
        pal += bytes(2 * cpp)
        npg += 1
        # the page count is the last u16 of whichever header closed
        struct.pack_into('<H', pal, hdr - 2, npg)
    off = hdr + 2 * plan.pal_page * cpp
    for i in range(cpp):
        struct.pack_into('<H', pal, off + 2 * i, NEAR_BLACK_555)
    parts[SECTION_PALETTE] = bytes(pal)
    # Re-read it the way every other reader will, and refuse if it no longer
    # closes -- a palette that does not parse is worse than a coloured bar.
    palette_block(parts[SECTION_PALETTE])

    # 2. the tiles: nothing but the palette_ID byte.
    sec9 = bytearray(parts[SECTION9])
    for t in plan.tiles:
        sec9[t.off + T_PAL] = plan.pal_page
    parts[SECTION9] = bytes(sec9)
    return parts


# ------------------------------------------------------------- the flevel pass
def apply_to_flevel(archive, payloads, encode=None, log=print, on=None):
    """
    Same contract as `ff7nx_ws.apply_to_flevel` and `ff7nx_bgkey`'s: a field
    already in `payloads` is taken from there, so this composes with the mod
    replacement passes rather than competing with them.

    MUST RUN AFTER the field-background repack -- see ORDERING above, and
    `build._build_flevel`, which calls this between `_convert_field_backgrounds`
    and `_bake_widescreen_ranges`. An earlier draft of this docstring said
    BEFORE; that was wrong and contradicted both ORDERING and the wiring.

    Raises nothing. A field that will not parse, or for which no free cell or
    free index exists, is counted in `stats['refused']` and left exactly as
    it was. A margin colour is not worth failing a build over.
    """
    import lgp

    on = enabled() if on is None else on
    stats = {'on': on, 'read': 0, 'changed': 0, 'tiles': 0, 'nothing': 0,
             'appended': 0, 'depth2': 0, 'stray': 0, 'refused': [],
             'plans': {}}
    if not on:
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
            plan, parts, surv = plan_field(name, raw, lgp)
            stats['read'] += 1
            stats['plans'][name] = plan
            stats['depth2'] += plan.n_depth2
            if plan.refusal:
                stats['refused'].append((name, plan.refusal))
                continue
            if not plan.tiles:
                stats['nothing'] += 1
                continue
            new = apply_plan(plan, parts, surv)
            payloads[name] = encode(lgp.join_sections(new))
            stats['changed'] += 1
            stats['tiles'] += len(plan.tiles)
            stats['appended'] += 1 if plan.appended else 0
            stats['stray'] += plan.n_stray
        except Exception as exc:                                # noqa: BLE001
            stats['refused'].append((name, '%s: %s'
                                     % (type(exc).__name__, str(exc)[:60])))
            continue

    if stats['refused']:
        log('  ! margin black: %d field(s) not changed (%s)'
            % (len(stats['refused']),
               ', '.join('%s: %s' % s for s in stats['refused'][:3])))
    return stats


def summarise(stats):
    if not stats or not stats.get('on'):
        return ''
    return ('margin black: %d filler tile(s) in %d of %d field(s) moved to a '
            'near-black palette page (%d appended; %d field(s) had no flat '
            'margin filler; %d truecolor filler tile(s) left%s%s)'
            % (stats['tiles'], stats['changed'], stats['read'],
               stats['appended'], stats['nothing'], stats['depth2'],
               '; %d stray margin tile(s) adopted the appended ID'
               % stats['stray'] if stats.get('stray') else '',
               ', %d refused' % len(stats['refused'])
               if stats['refused'] else ''))


# ------------------------------------------------------------------- verify
def render_margin(raw, lgp_mod):
    """
    (left_pixels, right_pixels, inside_rgb) for one field, as this tree's own
    renderer would draw layer 1. Used by `verify_flevel` -- the check that
    says whether the archive really changed, without a console.
    """
    parts = lgp_mod.split_sections(raw)
    cols, hdr, npg, cpp = palette_colours(parts[SECTION_PALETTE])
    r = ((cols & 0x1F) << 3) | ((cols & 0x1F) >> 2)
    g = (((cols >> 5) & 0x1F) << 3) | (((cols >> 5) & 0x1F) >> 2)
    b = (((cols >> 10) & 0x1F) << 3) | (((cols >> 10) & 0x1F) >> 2)
    rgb = np.stack([r, g, b], -1).astype(np.uint8)

    sec9 = parts[SECTION9]
    surv = DC.survey(sec9)
    pages = {p.slot: p for p in surv['pages']}
    W = H = 640
    img = np.zeros((H, W, 3), np.uint8)
    drawn = np.zeros((H, W), bool)
    cache = {}
    for layer, offs in DC.walk_layers(sec9, surv['back_start'],
                                      surv['tex_start']):
        if layer != 1:
            continue
        for o in offs:
            slot = sec9[o + T_TEX]
            p = pages.get(slot)
            if p is None:
                continue
            if slot not in cache:
                cache[slot] = page_array(p)
            arr, k = cache[slot]
            blk = source_block(arr, k, sec9[o + T_SRCX], sec9[o + T_SRCY])
            if blk is None:
                continue
            dx = struct.unpack_from('<h', sec9, o + T_DSTX)[0] + 320
            dy = struct.unpack_from('<h', sec9, o + T_DSTY)[0] + 320
            if not (0 <= dx <= W - TILE and 0 <= dy <= H - TILE):
                continue
            if p.depth == 1:
                pid = sec9[o + T_PAL]
                if pid >= npg:
                    continue
                px = rgb[pid][blk]
                m = blk != 0
            else:
                v = blk[::k, ::k]
                px = np.stack([((v >> 11) & 0x1F) << 3,
                               ((v >> 5) & 0x3F) << 2,
                               (v & 0x1F) << 3], -1).astype(np.uint8)
                m = v != 0
            sub = img[dy:dy + TILE, dx:dx + TILE]
            sub[m] = px[m]
            drawn[dy:dy + TILE, dx:dx + TILE] |= m
    return img, drawn


if __name__ == '__main__':
    import argparse

    ap = argparse.ArgumentParser(
        description='plan (and optionally verify) the margin-black pass')
    ap.add_argument('flevel')
    ap.add_argument('--fields', nargs='*', default=None)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--verbose', action='store_true')
    a = ap.parse_args()

    import lgp
    arc = lgp.Archive(a.flevel)
    names = sorted(n for n in arc.names() if arc.is_field(arc.index[n]))
    if a.fields:
        names = [n for n in names if n in a.fields]
    if a.limit:
        names = names[:a.limit]

    n_ok = n_ref = n_none = n_tiles = 0
    reasons = defaultdict(int)
    for name in names:
        try:
            plan, parts, surv = plan_field(name, arc.decompressed(
                arc.index[name]), lgp)
        except Exception as exc:                                # noqa: BLE001
            n_ref += 1
            reasons['%s' % type(exc).__name__] += 1
            if a.verbose:
                print('%-12s RAISED   %s: %s' % (name, type(exc).__name__,
                                                 str(exc)[:60]))
            continue
        if plan.refusal:
            n_ref += 1
            reasons[plan.refusal.split(':')[0][:40]] += 1
        elif plan.tiles:
            n_ok += 1
            n_tiles += len(plan.tiles)
        else:
            n_none += 1
        if a.verbose or (a.fields and len(names) <= 12):
            print(plan.line())

    print('\n---- %d field(s)' % len(names))
    print('planned        %d field(s), %d filler tile(s)' % (n_ok, n_tiles))
    print('nothing to do  %d' % n_none)
    print('refused        %d' % n_ref)
    for why, k in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print('   %5d  %s' % (k, why))
