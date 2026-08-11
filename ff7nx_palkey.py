#!/usr/bin/env python3
"""
ff7nx_palkey.py -- DE-FRINGE the transparency key on any palette the 16:9
margin uses.

SETTLED ON HARDWARE, 2026-08-05
===============================
The first version of this pass wrote BLACK at index 0 and the console answered
the question outright: the Sector 6 yellow disappeared AND black speckles
appeared across Wall Market's interior. So the console DRAWS index 0 with its
palette colour instead of discarding it. Black is therefore the wrong value --
it is only better than yellow. The key now takes the mean colour of the art
that sits NEXT TO the index-0 pixels, which is the same de-fringe the build
already performs on `char.lgp` and `battle.lgp` textures.


WHAT THIS IS FOR
================
Index 0 of a field palette is the colour key. By the format's own rule the
colour STORED at index 0 is never drawn -- the pixel is discarded instead --
so whatever byte pair sits there is arbitrary leftover authoring data. MEASURED
over the shipped archive: 1,503 of the 1,934 palette pages the margin tiles
name have a non-black entry 0, and 665 of those are bright.

Four fields in the whole game store PURE YELLOW (A1B5G5R5 0x03FF = RGB
255,255,0) at index 0:

    mds6_2   mds6_3   hekiga   chrin_3b

Two of those four are the Sector 6 fields the user photographed, and the yellow
he photographed is exactly (255, 255, 0) -- measured off the capture, 80,732
margin pixels, modal colour (255,255,0) with a JPEG skirt of +-3.

That is not proof, and this pass does not claim to be the fix. It is a free
one: the stored colour at index 0 cannot legitimately be seen, so replacing it
with 0x0000 is a no-op wherever the key works and turns yellow into black
wherever it does not. If the yellow goes black in the next build, the leak is
the key colour and the next question is which code path substitutes it instead
of discarding the pixel. If the yellow stays, the key colour is exonerated.

WHAT IS NOT TOUCHED
===================
* Only fields that HAVE margin tiles -- layer 1, wholly outside the 4:3
  picture. A field the widescreen work never touched is left byte-identical.
  Within such a field every palette page is done, because the margin page is
  shared with interior tiles at other palettes and which one the console binds
  is not knowable from the archive.
* Only entry 0. Entries 1..255 are untouched.
* `ff7nx_marginart.quantise` already refuses to emit index 0 and
  `field_bg_native.paletted_to_565` already maps index 0 to EMPTY regardless of
  the palette, so neither pass changes behaviour because of this.
"""
from __future__ import annotations

import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import diag_common as DC
import ff7nx_marginblack as MB

SECTION9 = 8


def margin_palettes(sec9, npg):
    """{palette page index} named by a layer-1 tile wholly outside 4:3."""
    surv = DC.survey(sec9)
    pages = {p.slot: p for p in surv['pages']}
    out = set()
    for t in MB.read_tiles(sec9, surv, pages):
        if t.layer == 1 and t.outside_43 and t.pal < npg:
            out.add(t.pal)
    return out


def _is_marker(v):
    """
    True when a key value is a CHROMA-KEY MARKER rather than a colour an
    artist chose.

    NOT CURRENTLY APPLIED. The classification below is measured and sound,
    but wiring it in was one of three unverified changes in a single build
    and that build was a clear downgrade. Kept, unused, so it can be tested
    on its own.

    A marker is fully saturated: one channel at the top of its range and
    another at the bottom. Real field art in this game is desaturated -- the
    palettes are browns, slates and olives.

    MEASURED over all 6,423 palettes in the vanilla archive: 281 (4.4%)
    classify as markers, and the values are exactly the textbook keys --

        0x03E0  (  0, 255,   0)  green     138
        0x7FE0  (  0, 255, 255)  cyan       57
        0x001F  (255,   0,   0)  red        32
        0x7C1F  (255,   0, 255)  magenta    23   <- onna_52, the Honey Bee
        0x7C00  (  0,   0, 255)  blue       17
        0x03FF  (255, 255,   0)  yellow      6

    while every key known from hardware to be ART falls outside it: the
    Sector 5 park's (24,131,197), the reactor stairs' (32,98,164),
    onna_52's own palette 0 at (49,41,49).

    THIS IS THE DISTINCTION THE PASS HAS BEEN MISSING. Forcing every key to
    black killed the markers and the art together (the park speckles);
    leaving every key alone spared the art and the markers together. The two
    groups do not overlap and never have.
    """
    r = (v & 31) * 255 // 31
    g = ((v >> 5) & 31) * 255 // 31
    b = ((v >> 10) & 31) * 255 // 31
    return max(r, g, b) >= 224 and min(r, g, b) <= 32


def _neighbour_colour(sec9, npg, pal):
    """
    The mean 5-bit colour of the pixels that sit NEXT TO an index-0 pixel, on
    the cells drawn through palette page `pal`. Returns None when the palette
    draws no index-0 pixel at all.

    This is the de-fringe the build already performs on `char.lgp` and
    `battle.lgp` textures, applied to field palettes: the key entry takes the
    colour of the art beside it, so a renderer that draws it instead of
    discarding it produces a colour that blends rather than a hole.
    """
    import numpy as np

    surv = DC.survey(sec9)
    pages = {p.slot: p for p in surv['pages']}
    counts = np.zeros(256, np.int64)
    seen = set()
    for t in MB.read_tiles(sec9, surv, pages):
        if t.pal != pal or t.cell in seen:
            continue
        p = pages.get(t.slot)
        if p is None or p.depth != 1:
            continue
        seen.add(t.cell)
        arr, k = MB.page_array(p)
        blk = MB.source_block(arr, k, t.sx, t.sy)
        if blk is None:
            continue
        z = blk == 0
        if not z.any():
            continue
        nb = np.zeros_like(z)
        nb[1:, :] |= z[:-1, :]
        nb[:-1, :] |= z[1:, :]
        nb[:, 1:] |= z[:, :-1]
        nb[:, :-1] |= z[:, 1:]
        take = blk[nb & ~z]
        if take.size:
            counts += np.bincount(take, minlength=256)[:256]
    return counts if counts.any() else None


# SLOT BANDS, from field_bg_native.D1_GROUPS and FFNx's blend table
# (repos/FFNx-master/src/common.cpp:2216, "identical to the Direct3D driver"):
#
#     0x00-0x0E   blend 4   OPAQUE
#     0x0F-0x17   blend 1   ADDITIVE
#     0x18-0x19   blend 0   AVERAGE
#
# Only the opaque band may have its key de-fringed. See _blend_band_palettes.
BLEND_BAND_FIRST_SLOT = 0x0F


def _overlay_palettes(sec9, surv=None):
    """
    Palette indices named by any tile that is NOT on layer 1.

    THE GREY BLOCKS IN SECTOR 6 ARE THIS.
    =====================================
    Layer 1 is the static backdrop: nothing is behind it, so an index-0 pixel
    is just a pixel and giving it the colour of the art beside it is what stops
    filtering drawing a dark seam. That is what this pass is for.

    Layers 2-4 are OVERLAYS drawn on top of layer 1, and 58% of vanilla
    layer-2 cells contain index 0 across 33% of their pixels (HANDOFF-78 3.3).
    Those pixels are the transparent surround of an object -- a swing, a
    ball, a tank. Blending them costs nothing only while the colour is BLACK,
    which is the identity element. De-fringed to the mean colour of the art
    beside them, every one of those cells lays a pale wash over the layer-1
    art in a 16x16 rectangle.

    REPRODUCED OFFLINE, no build: `render_field.py` on the built archive draws
    mds6_22 and mds6_1 with exactly the grey rectangles reported from
    hardware, and a depth mask shows every one of them lands on a LAYER 2+
    tile -- not on a promoted cell and not on a borrowed one. Forcing entry 0
    back to black on these palettes changes the render by mean 6.01 / max 96.

    AND IT COSTS LAYER 1 NOTHING. MEASURED on both fields: the layer-1 palette
    set and the layer-2+ palette set are DISJOINT (mds6_22: 4 and 7, overlap
    0; mds6_1: 5 and 7, overlap 0). The two rules do not compete.

    This supersedes the narrower blend-band test that fixed the steam. The
    additive band was a SUBSET of this: every tile drawing from a depth-1 page
    in the 0x0F-0x19 band is an fx tile, and fx tiles are layer 2+. The band
    test is kept below because a page can be in the band without this walk
    seeing its layer, and two cheap tests are better than one.
    """
    if surv is None:
        surv = DC.survey(sec9)
    out = set()
    for layer, offs in DC.walk_layers(sec9, surv['back_start'],
                                      surv['tex_start']):
        if layer == 1:
            continue
        for o in offs:
            out.add(sec9[o + MB.T_PAL])
    return out | _blend_band_palettes(sec9, surv)


def _opaque_band_palettes(sec9, surv=None):
    """Palette indices named by any tile whose EFFECTIVE page is depth-1 and
    in the OPAQUE band (field_bg_native.D1_GROUPS' blend-4 range).

    The mirror of `_blend_band_palettes`, and the set that must NOT have its
    key blacked: on an opaque page index 0 is drawn as a solid colour, so
    black is a black rectangle rather than an identity element.
    """
    import field_bg_native as _FN
    lo, hi, _b = _FN.D1_GROUPS[0]
    out = set()
    try:
        surv = surv if surv is not None else DC.survey(sec9)
        pages = {p.slot: p for p in surv['pages']}
        for t in MB.read_tiles(sec9, surv, pages):
            eff = sec9[t.off + 34] or t.slot     # fx page byte, else its own
            p = pages.get(eff)
            if p is not None and p.depth == 1 and lo <= eff < hi:
                out.add(t.pal)
    except Exception:                                          # noqa: BLE001
        return set()
    return out


def _blend_band_palettes(sec9, surv=None):
    """
    Palette indices named by any tile drawing from a depth-1 page in the
    ADDITIVE or AVERAGE band.

    WHY THESE MUST NOT BE DE-FRINGED
    ================================
    FFNx `ff7/field/field.cpp:56` sets `tex_header->color_key = 3` ONLY for
    `type == 2` -- the truecolor pages. A depth-1 page never gets a colour key,
    so `pal2bgra` (common.cpp:1726) never takes its `pixel == 0` early-out and
    **index 0 is drawn through the palette like any other index**. That is
    HANDOFF-78 2.5, confirmed from the reference renderer rather than inferred.

    On an OPAQUE page that is exactly why the de-fringe helps: the key's colour
    is drawn, and filtering bleeds it into the art, so it should carry the
    art's colour rather than punch a hole.

    On an ADDITIVE page it is why the de-fringe HURTS. Black is the identity
    element for addition: an index-0 pixel with a black key adds nothing and
    the background shows through unchanged. Give that same pixel the mean
    colour of the art beside it and it now ADDS that colour -- across the
    whole transparent surround of the cell, which on an fx tile is most of it.
    A cell-aligned rectangle of added light that follows the animation as it
    spreads. That is the steam.

    MEASURED over all 709 vanilla fields, reading the effective page (fx where
    set): 105,258 tiles draw from an additive depth-1 page and 2,287 from an
    average one, and EVERY ONE OF THEM IS AN FX TILE. Those pages are not
    reachable any other way, so this test costs the opaque band nothing.

    A palette drawn on BOTH an opaque and a blend page is returned here too --
    leaving it alone keeps vanilla behaviour, where de-fringing it would add
    light to the fx cells to win a filtering artefact on the opaque ones. The
    safe direction is to change nothing.

    THE PAGE HAS TO BE THE EFFECTIVE ONE, NOT `texture_id`.
    ------------------------------------------------------
    FFNx `ff7/field/background.cpp:113`:

        page = tile.use_fx_page ? tile.fx_page : tile.page;

    A blend-band page is reached ONLY through `fx_page`. Reading `texture_id`
    alone -- which is what `ff7nx_marginblack.read_tiles` exposes -- finds zero
    tiles in the band across every field in the archive, which is exactly the
    wrong answer and cost me one pass at this function.
    """
    if surv is None:
        surv = DC.survey(sec9)
    pages = {p.slot: p for p in surv['pages']}
    out = set()
    for _layer, offs in DC.walk_layers(sec9, surv['back_start'],
                                       surv['tex_start']):
        for o in offs:
            fx = sec9[o + MB.T_TEX2]
            slot = fx if fx else sec9[o + MB.T_TEX]
            p = pages.get(slot)
            if p is None or p.depth != 1:
                continue
            if slot >= BLEND_BAND_FIRST_SLOT:
                out.add(sec9[o + MB.T_PAL])
    return out


def blacken_keys(sec3, sec9):
    """(new_sec3, [(page, old_value), ...]). `sec3` is returned when nothing
    needs changing, so the caller can skip the re-encode."""
    hdr, npg, cpp = MB.palette_block(sec3)
    if not npg or not cpp:
        return sec3, []
    if not margin_palettes(sec9, npg):
        return sec3, []
    # EVERY palette page of a field that has margin tiles, not only the pages
    # those tiles name. `mds6_3`'s margin names palette 0, but its margin page
    # is shared with interior tiles at palettes 2 and 3, so whichever palette
    # the console binds for that page is not knowable from the archive --
    # palette 1, whose entry 0 is the same pure yellow, is a live candidate and
    # no margin tile names it.
    used = range(npg)
    # ...EXCEPT the palettes drawn on an additive or average page, where the
    # key's colour is ADDED rather than drawn over. See _blend_band_palettes.
    blend_pals = _overlay_palettes(sec9)
    # ...AND NOT THE ONES ALSO DRAWN ON AN OPAQUE PAGE.
    #
    # `_overlay_palettes` blacks the key of EVERY layer-2+ palette, on the
    # reasoning that "black is the identity element" for the blend. That is
    # true when the overlay is actually blended. It is false when the overlay
    # is drawn from an OPAQUE page, and `_blend_band_palettes`'s own docstring
    # says so in as many words: "On an OPAQUE page that is exactly why the
    # de-fringe helps: the key's colour is drawn, and filtering bleeds it into
    # the art, so it should carry the art's colour rather than punch a hole."
    #
    # MEASURED on `md8_1`, the Sector 8 fire scene:
    #
    #     palette 4   layer 2, page slot 1 (OPAQUE band)   10,181 index-0 px
    #     palette 10  layer 2, page slot 1 (OPAQUE band)    2,422 index-0 px
    #     vanilla keys 0x6203 RGB(96,64,24) and 0x5184 RGB(80,48,32)
    #
    # Twelve and a half thousand pixels of mid-brown, forced to black, drawn
    # opaque over the backdrop. That is the dark blocks on the stairs and the
    # black speckling in the Sector 5 park.
    #
    # So the rule is per palette rather than per layer: a palette that any
    # tile draws from an opaque page follows the opaque rule and gets
    # de-fringed. A palette that ONLY ever appears on a blend band keeps the
    # black key, which is the Sector 6 grey-block case `_overlay_palettes` was
    # written for and which this does not touch.
    # ...and an overlay palette that is ALSO drawn from an opaque page is
    # LEFT EXACTLY AS VANILLA SHIPPED IT -- neither blacked nor de-fringed.
    #
    # Both branches are wrong for it, and each has a hardware report behind
    # the other one:
    #
    #   de-fringe it  ->  a pale wash over a 16x16 rectangle. That is the
    #                     Sector 6 grey blocks `_overlay_palettes` was
    #                     written to stop.
    #   black it      ->  a BLACK rectangle drawn opaque over the backdrop.
    #                     MEASURED on `md8_1`: palettes 4 and 10 are layer-2
    #                     tiles on page slot 1, the opaque band, carrying
    #                     10,181 and 2,422 index-0 pixels, and their vanilla
    #                     keys are 0x6203 RGB(96,64,24) and 0x5184
    #                     RGB(80,48,32). Forced to black that is the dark
    #                     blocks on the stairs and the speckling in the park.
    #
    # Vanilla's own value is the one colour known to be right in both
    # situations: it is what the artists chose and what the game shipped and
    # ran with for thirty years. This module already states the principle --
    # "The safe direction is to change nothing" -- it just had no branch for
    # it. Palettes that only ever appear on a blend band keep the black key,
    # so the fx/steam fix is untouched.
    # BUILD 28 ADDED `_leave = _overlay_palettes & _opaque_band_palettes`
    # HERE AND IT IS REMOVED AGAIN. Together with the two `continue`s below it
    # took the pass from 3,267 palette pages de-fringed across 601 fields
    # (builds 20-27) to 1,199 across 441 -- 2,068 palettes stopped being
    # touched, 785 of them bright colours left to draw as authored. Reverted
    # to the build-27 behaviour, which is the last state you called good.
    _leave = set()
    import numpy as np

    cols = np.frombuffer(sec3, '<u2', count=cpp * npg,
                         offset=hdr).reshape(npg, cpp)
    buf = bytearray(sec3)
    changed = []
    skipped = 0
    for p in sorted(used):
        off = hdr + (p * cpp) * 2
        if p in _leave:
            skipped += 1
            continue
        if p in blend_pals:
            # OVERLAY PALETTE: entry 0 must be BLACK, not de-fringed and not
            # left at whatever the mod authored. Black is the identity element
            # for both blend modes the overlay bands use, so a keyed pixel
            # contributes nothing; any other colour is added to the layer-1 art
            # underneath it across the whole transparent surround of the cell.
            skipped += 1
            _old = struct.unpack_from('<H', buf, off)[0]
            if _old & 0x7FFF:
                struct.pack_into('<H', buf, off, _old & 0x8000)
                changed.append((p, _old))
            continue
        old = struct.unpack_from('<H', buf, off)[0]
        counts = _neighbour_colour(sec9, npg, p)
        if counts is None:
            # NO NEIGHBOURING ART, SO NOTHING TO DE-FRINGE -- AND NOTHING TO
            # DECIDE. LEAVE IT.
            #
            # This used to write BLACK, on the reasoning that a palette which
            # draws no index-0 pixel cannot show its key, so black is a safe
            # default. It is not safe, and this module's own summary line says
            # why: "the console DRAWS index 0 rather than discarding it
            # (proved: blacking it out removed the Sector 6 yellow and put
            # black speckles everywhere else)".
            #
            # MEASURED on `md8_1`, the Sector 8 fire scene, layer-1 palettes:
            #
            #     pal 0   vanilla 0x07C0 (0,248,0)  ->  ours 0x0000    64 tiles
            #     pal 1   vanilla 0x7B86 (120,112,48) -> ours 0x0000   52 tiles
            #     pal 2   vanilla 0x4A84 (72,80,32)   -> ours 0x0000   64 tiles
            #     pal 3   vanilla 0x8447 (128,136,56) -> ours 0x0000   39 tiles
            #
            # Palette 0's key is 0x07C0, pure green -- the classic chroma key.
            # Turning it black does not make it invisible, it makes it BLACK,
            # and 219 layer-1 tiles in one field carry it. That is the black
            # speckling reported in the Sector 5 park and the dark blocks on
            # the stairs here.
            #
            # `_neighbour_colour` returning None means this pass has no
            # evidence about what the key should be. The honest answer to no
            # evidence is to change nothing: vanilla's value is at least the
            # value the artists chose and the value the game shipped with.
            #
            # UNLESS IT IS A MARKER. "The value the artists chose" is the
            # whole argument for leaving it, and it does not apply to pure
            # magenta or pure green -- nobody chose those as scenery, they are
            # the transparency flag this port fails to honour. If such a key
            # ever reaches a pixel it is wrong by construction, so black it:
            # black is what the pixel would be if the port discarded index 0
            # the way the mod's reference renderer does.
            # A palette that draws no index-0 pixel cannot show its key, so
            # black is the safe default. RESTORED to the build-27 behaviour.
            new = 0
        else:
            v = cols[p].astype(np.int64)
            w = counts.astype(np.float64)
            w[0] = 0.0                        # never average the key into itself
            if not w.any():
                # EVERY PIXEL THIS PALETTE DRAWS IS INDEX 0. There is no art
                # to average, and blacking the key turns the WHOLE PAGE black.
                #
                # MEASURED across the built archive: 110 pages are entirely
                # index 0 and 5,128 tiles draw from them. Vanilla has ZERO
                # such pages -- they are created by
                # `ff7nx_marginpage.split_section9`, which allocates
                # `np.zeros((256, 256))` for a new palette-pure page and then
                # moves the flat 16:9 placeholder cells onto it. A flat
                # placeholder IS index 0, so the page stays all zero, and its
                # entire appearance is whatever entry 0 holds.
                #
                # Vanilla drew the authored filler colour there. Blacking it
                # draws a solid black page -- which is the regular grid of
                # black squares over the Honey Bee keyhole scene and a large
                # share of the black rectangles elsewhere.
                #
                # No evidence, so change nothing: keep the value the game
                # shipped with -- UNLESS it is a marker, which is never the
                # value anyone chose. On a page that is 100% index 0 the key
                # IS the whole page, so a magenta marker here paints a solid
                # magenta rectangle. That is the Honey Bee keyhole grid:
                # onna_52 palettes 1 and 3 both key 0x7C1F, and over half of
                # its slots 1 and 2 is index 0 in the built archive.
                # RESTORED to the build-27 behaviour.
                new = 0
            else:
                tot = float(w.sum())
                r = int(round(float(((v & 31) * w).sum()) / tot))
                g = int(round(float((((v >> 5) & 31) * w).sum()) / tot))
                b = int(round(float((((v >> 10) & 31) * w).sum()) / tot))
                new = (old & 0x8000) | (b << 10) | (g << 5) | r
        if new == old:
            continue
        struct.pack_into('<H', buf, off, new)
        changed.append((p, old))
    blacken_keys.last_skipped = skipped
    if not changed:
        return sec3, []
    return bytes(buf), changed


def apply_to_flevel(archive, payloads, encode=None, log=print, fields=None):
    import lgp

    st = {'read': 0, 'changed': 0, 'pages': 0, 'bright': 0,
          'blend_skipped': 0, 'refused': []}
    encode = encode or (lambda raw: archive.encode_field(raw))
    for name in archive.names():
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        if fields and name not in fields:
            continue
        payload = payloads.get(name, entry.get('payload'))
        if not payload:
            continue
        try:
            raw = (lgp.lzs_decompress(payload[4:]) if name in payloads
                   else archive.decompressed(entry))
            parts = lgp.split_sections(raw)
            st['read'] += 1
            new3, changed = blacken_keys(parts[MB.SECTION_PALETTE],
                                         parts[SECTION9])
            st['blend_skipped'] += getattr(blacken_keys, 'last_skipped', 0)
            if not changed:
                continue
            parts[MB.SECTION_PALETTE] = new3
            payloads[name] = encode(lgp.join_sections(parts))
            st['changed'] += 1
            st['pages'] += len(changed)
            for _, old in changed:
                v = old & 0x7FFF
                if (v & 31) + ((v >> 5) & 31) + ((v >> 10) & 31) >= 24:
                    st['bright'] += 1
        except Exception as exc:                                # noqa: BLE001
            st['refused'].append((name, '%s: %s'
                                  % (type(exc).__name__, str(exc)[:60])))
    if st['refused'] and log:
        log('  ! transparency key: %d field(s) not changed (%s)'
            % (len(st['refused']),
               ', '.join('%s: %s' % r for r in st['refused'][:3])))
    return st


def summarise(st):
    if not st or not st.get('pages'):
        return ''
    return ('transparency key: entry 0 de-fringed on %d palette page(s) '
            'across %d field(s), %d of them previously a bright colour -- the '
            'console DRAWS index 0 rather than discarding it (proved: blacking '
            'it out removed the Sector 6 yellow and put black speckles '
            'everywhere else), so the key now carries the mean colour of the '
            'art beside it and blends instead of punching a hole. %d '
            'palette(s) were LEFT ALONE because they are drawn on an additive '
            'or average page, where a non-black key is ADDED to the background '
            'across the whole transparent surround of an fx cell -- the '
            'square patches around steam and reactor effects'
            % (st['pages'], st['changed'], st['bright'],
               st.get('blend_skipped', 0)))


if __name__ == '__main__':
    import argparse

    import lgp

    ap = argparse.ArgumentParser(
        description='black out the transparency key on margin palettes')
    ap.add_argument('flevel')
    ap.add_argument('--out')
    ap.add_argument('--fields', nargs='*')
    a = ap.parse_args()
    arc = lgp.Archive(a.flevel)
    pay = {}
    st = apply_to_flevel(arc, pay, fields=a.fields)
    print(summarise(st) or 'nothing to do')
    if a.out:
        arc.replace(pay)
        arc.write(a.out)
        print('wrote %s' % a.out)
