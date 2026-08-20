#!/usr/bin/env python3
"""
ff7nx_greenkey.py -- Square's chroma-key palette entry is DRAWN, and it is
bright green.

THE REPORT, from hardware after build 125, on `mds7plr1`: bright green specks
in the upper-left corner of the fence. FINDINGS-261 has the measurement; in
one paragraph:

    mds7plr1 palette 0, entry 1  =  0x07C0  =  RGB(0, 251, 0)

Pure green, the classic chroma-key marker, and **identical in vanilla and in
our build** -- Square shipped it. Entry 0 is the transparency key and is never
drawn on layer 2; entry 1 has no such protection and IS drawn. It sits on
layer 1, underneath a layer-2 fence, and shows through the fence's mesh holes.
That is why Patrick described it as "what is displayed behind these textures":
it is exactly that, and the fence was innocent.

WHY IT SURVIVES INTO OUR BUILD AND NOT INTO FFNx
================================================
FFNx replaces the whole page with the mod's DDS at draw time and never renders
a paletted index at all. We render them wherever a cell stays paletted, and
this cell stays paletted for a specific, correct reason:

    cell (0,0) of page 0 contains 35 texels of index 0, so `rec['key']` is
    true, and `field_bg_dense`'s layer-1 colour-key veto refuses to promote a
    keyed layer-1 cell to truecolor. See PROMOTE_LAYER1_KEY.

`ff7nx_marginart` does not fill it either, and also correctly: it fills MARGIN
cells, and this one is interior. MEASURED: marginart rewrites 36,823 texels of
that page and leaves cell (0,0) **byte-identical to vanilla**.

So three passes each did the right thing and the texel fell between them.

WHAT THIS PASS DOES
===================
For every DRAWN texel on a paletted page whose palette colour is a marker,
re-index it to the mod's own art, quantised through that texel's own palette.

Six conditions, and every one of them is a thing that would otherwise change
a picture nobody complained about:

  1. DEPTH-1 PAGES ONLY. A truecolor page has no indices and no palette.
  2. INDEX 0 IS NEVER TOUCHED, read or written. It is the colour key, it
     decides the silhouette, and builds 119-125 are all about not moving it.
     A replacement index of 0 is refused for the same reason.
  3. THE COLOUR MUST BE A MARKER -- `MARKER_G`/`MARKER_RB`, i.e. a saturated
     green no natural scene contains. This is deliberately not "unusual
     colour"; it is one specific value family that means "you were never
     meant to see this".
  4. THE MOD MUST SHIP ART for that (page, palette), and the art must be
     OPAQUE at the texel. We replace a marker with REAL ART or we leave it
     alone -- never with a guess, never with a blend.
  5. THE REPLACEMENT IS CHOSEN FROM NON-MARKER, NON-ZERO ENTRIES ONLY, so
     this pass can neither re-emit a marker nor key a texel.
  6. NOTHING ELSE MOVES. No page, no tile, no palette, no byte of length --
     this rewrites index bytes in place and nothing else.

The population is tiny and the direction is one-way: a texel that renders a
colour the artist marked as "not for display" gets the colour the mod puts
there. `_kgreen.py` is the gate.

`SEVENTH_NX_NO_GREENKEY=1` disables the pass entirely.
"""
from __future__ import annotations

import os

import numpy as np

import diag_common as DC
import field_bg_dense as FD
import field_bg_pagecap as PC
import field_bg_repack as RP
import ff7nx_marginblack as MB

ENV_OFF = 'SEVENTH_NX_NO_GREENKEY'

# THE MARKER TEST, AND IT IS DELIBERATELY NARROW.
#
# `mds7plr1`'s is RGB(0, 251, 0). A test like "unusual colour" or "high
# saturation" would sweep up real art -- Cosmos's palettes contain plenty of
# saturated greens in foliage and mako lighting. This asks for a colour with
# essentially NO red and NO blue and a very high green, which is what a
# chroma-key marker is and what a rendered scene is not.
MARKER_G = int(os.environ.get('SEVENTH_NX_GREENKEY_G') or 120)
MARKER_RB = int(os.environ.get('SEVENTH_NX_GREENKEY_RB') or 60)


def enabled():
    return os.environ.get(ENV_OFF) != '1'


def _rgb8(a):
    """R5G6B5 -> (...,3) uint8 by the engine's bit replication."""
    u = np.asarray(a).astype(np.uint32)
    r = (u >> 11) & 31
    g = (u >> 5) & 63
    b = u & 31
    return np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4),
                     (b << 3) | (b >> 2)], -1).astype(np.int16)


def marker_entries(pal565):
    """(npal, nentry) bool -- which palette entries are chroma-key markers."""
    c = _rgb8(pal565)
    return ((c[..., 1] >= MARKER_G) & (c[..., 0] < MARKER_RB)
            & (c[..., 2] < MARKER_RB))


def fix_section9(sec9, sec3, art_for, log=lambda *_: None):
    """
    Section 9 with drawn marker texels re-indexed to the mod's art.

    Returns `(sec9, stats)`. `sec9` is a new bytearray of the SAME LENGTH, or
    the input unchanged when nothing qualifies.
    """
    st = {'cells': 0, 'texels': 0, 'no_art': 0, 'not_opaque': 0,
          'pages': 0}
    if not enabled() or art_for is None:
        return sec9, st
    try:
        pal565, _npg, _cpp = FD._pal_rgb(sec3)
    except Exception:                                          # noqa: BLE001
        return sec9, st
    if pal565 is None or not len(pal565):
        return sec9, st
    mk = marker_entries(pal565)
    if not mk.any():
        return sec9, st

    surv = DC.survey(sec9)
    pages = {p.slot: p for p in surv['pages'] if p is not None}
    tiles = MB.read_tiles(sec9, surv, pages)

    # Palette candidates: everything that is neither a marker nor index 0.
    # Condition 5. Built once per palette rather than per texel.
    pal_rgb = _rgb8(pal565).astype(np.int32)

    out = bytearray(sec9)
    arrays, arts, dirty = {}, {}, {}
    for t in tiles:
        p = pages.get(t.slot)
        if p is None or p.depth != 1:
            continue                                            # 1
        pal = t.pal if t.pal < len(pal565) else len(pal565) - 1
        if not mk[pal].any():
            continue
        if t.slot not in arrays:
            try:
                arrays[t.slot] = PC._page_array(p).copy()
            except Exception:                                  # noqa: BLE001
                arrays[t.slot] = None
        a = arrays[t.slot]
        if a is None:
            continue
        blk = a[t.sy:t.sy + MB.TILE, t.sx:t.sx + MB.TILE]
        if blk.shape != (MB.TILE, MB.TILE):
            continue
        hit = (blk != 0) & mk[pal][blk]                         # 2, 3
        if not hit.any():
            continue
        key = (t.slot, pal)
        if key not in arts:
            try:
                arts[key] = art_for(t.slot, pal)
            except Exception:                                  # noqa: BLE001
                arts[key] = None
        im = arts[key]
        if im is None or getattr(im, 'amax', None) is None:
            st['no_art'] += int(hit.sum())                      # 4
            continue
        k = im.px // 256
        am = im.amax[t.sy * k:(t.sy + MB.TILE) * k,
                     t.sx * k:(t.sx + MB.TILE) * k]
        cm = im.cmax[t.sy * k:(t.sy + MB.TILE) * k,
                     t.sx * k:(t.sx + MB.TILE) * k]
        if am.shape != (MB.TILE * k, MB.TILE * k):
            st['no_art'] += int(hit.sum())
            continue
        if k > 1:                       # one value per INDEX, not per texel
            am = am.reshape(MB.TILE, k, MB.TILE, k).max(axis=(1, 3))
            cm = cm.reshape(MB.TILE, k, MB.TILE, k)[:, k // 2, :, k // 2]
        ok = hit & (am >= 128)                                  # 4
        st['not_opaque'] += int((hit & ~ok).sum())
        if not ok.any():
            continue
        want = _rgb8(cm[ok]).astype(np.int32)
        cand = ~mk[pal]
        cand[0] = False                                         # 5
        idx = np.nonzero(cand)[0]
        if not len(idx):
            continue
        d = ((want[:, None, :] - pal_rgb[pal][idx][None, :, :]) ** 2).sum(-1)
        blk[ok] = idx[d.argmin(axis=1)].astype(blk.dtype)
        st['cells'] += 1
        st['texels'] += int(ok.sum())
        dirty[t.slot] = True

    if not dirty:
        return sec9, st
    # LOCATING THE PAGE'S BYTES. `Page` carries its data but not its offset,
    # so the offset is found by searching for that exact blob inside the
    # texture block -- and the search is REQUIRED TO BE UNIQUE. A depth-1
    # page is 65,536 bytes; two distinct pages colliding is not a practical
    # risk, but "not a practical risk" is how this project acquires silent
    # corruption, so a second occurrence refuses the page instead.
    tex_start = surv.get('tex_start') or 0
    for slot in dirty:
        p = pages[slot]
        blob = bytes(p.data)
        at = sec9.find(blob, tex_start)
        if at < 0 or sec9.find(blob, at + 1) >= 0:
            st['unplaceable'] = st.get('unplaceable', 0) + 1
            log('  ! greenkey: page %d could not be placed uniquely' % slot)
            continue
        out[at:at + len(blob)] = arrays[slot].astype(np.uint8).tobytes()
        st['pages'] += 1
    if not st['pages']:
        return sec9, st
    return bytes(out), st
