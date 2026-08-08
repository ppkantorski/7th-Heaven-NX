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
    import numpy as np

    cols = np.frombuffer(sec3, '<u2', count=cpp * npg,
                         offset=hdr).reshape(npg, cpp)
    buf = bytearray(sec3)
    changed = []
    skipped = 0
    for p in sorted(used):
        off = hdr + (p * cpp) * 2
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
            # The palette draws no index-0 pixel anywhere, so the key is never
            # reachable. Black it out: it cannot be seen, and if some path we
            # have not found reaches it, black is the safe value.
            new = 0
        else:
            v = cols[p].astype(np.int64)
            w = counts.astype(np.float64)
            w[0] = 0.0                        # never average the key into itself
            if not w.any():
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
