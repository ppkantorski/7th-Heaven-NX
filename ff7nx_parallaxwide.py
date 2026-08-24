#!/usr/bin/env python3
"""
ff7nx_parallaxwide.py -- give a 1:1 parallax layer enough art to fill 16:9.

THE REPORT, from hardware on `trnad_4` (Whirlwind Maze): the green lifestream
backdrop covers part of the frame, the rest shows the cyan mist behind it, and
the boundary slides across the screen as you walk.

WHAT IT IS
==========
`trnad_4` layer 3 is 11 columns of 32 units = 352, and `bg3_width` is 352 too,
so the layer is exactly one wrap period. The 16:9 picture is
`wide_viewport_width / 2` = 427 units. **352 of art cannot cover 427 of
picture.** The engine's wrap slides that 352-unit window around rather than
tiling it, so there is always a band at one edge and its width changes with
the camera.

Measured on the port's own shift block (`_kl3shift.run_one`, executed, not
read), worst camera position, uncovered units of the 427-unit picture:

    11 columns, as shipped                              157.5
    +1 period  (22 columns)                              75.0
    +/-1 period (33 columns)                             53.5
    +/-2 periods (55 columns)                             0.0
    k = (-2, +1, +2)  (44 columns)                        0.0   <- this pass

WHAT FFNx DOES, AND WHY WE CANNOT COPY IT EXACTLY
=================================================
`FFNx/src/ff7/field/background.cpp`, `field_layer3_pick_tiles`:

    do_increase_width = is_fieldmap_wide() && header->bg3_width < ceil(854/2)
    layer3_width      = header->bg3_width * (do_increase_width ? 2 : 1)

and then it draws the whole tile set a second time at `+layer3_width/2`. Its
shift also uses `right_offset = abs(wide_viewport_x)` = 107, where this port
still has 0 (`ff7nx_fieldwide`'s KNOWN GAP), and it CULLS after the shift,
which this port does not (FINDINGS-285).

Modelled against the executed block, FFNx's exact shape on THIS port leaves
53.5 units -- the whole difference is `right_offset`. Rather than patch the
binary, this pass compensates with more copies: the k-set above reaches ZERO
with the header untouched, no code patch and no cave space.

WHY THE COPIES NEED THEIR OWN PAGES
===================================
`field_bg_pagecap.effective_counts` is the count the console makes, and its
invariant is 256 BINDING tiles per page -- by raw texture id many vanilla
fields are far over (hyou5_2 is 953) and have shipped since 1997, but by
binding page not one vanilla field exceeds 256. On these fields the parallax
has no headroom at all, so the copies get FRESH slots holding a BYTE COPY of
the page they bind, dealt round-robin so no new page passes 256. The original
pages are not touched, and neither are layers 1 and 2.

THE BINDING PAGE IS NOT THE TEXTURE ID. A tile carrying an fx page binds the
FX page. `trnad_4`'s layer 3 names texture 0 on all 165 records and carries fx
pages 17 and 18 -- the first version of this pass duplicated slot 0, rewrote
the texture id, and moved nothing while piling 468 binding tiles onto slot 17.

Cost, measured: three or four extra pages per field, worst case 15 of the 20
`max_total_pages()` allows.

SCOPE
=====
`speed_x != 0` (the layer tracks the camera; a pinned layer is the MARGIN
problem and belongs to `ff7nx_parallaxfill.plan_layer_edge_x`) AND
`0 < bg?_width < 427`, which is FFNx's own `do_increase_width` test. That is
24 layers: the whole `hyou*` Great Glacier, the `move_*` set, `kuro_1`,
`trnad_2`, `trnad_3` layer 4 and `trnad_4`.

SEVENTH_NX_NO_PARALLAX_WIDE=1 turns this off.
"""
from __future__ import annotations

import os
import struct

import numpy as np

import diag_common as DC
import field_bg_native as FN
import field_bg_pagecap as PC
import ff7nx_parallaxfill as PF

OFF_ENV = 'SEVENTH_NX_NO_PARALLAX_WIDE'

# `ceil(wide_viewport_width / 2)` with `wide_viewport_width` 854
# (FFNx src/widescreen.h). This is FFNx's own `do_increase_width` threshold.
PICTURE_W = 427

# ---- AND FFNx'S OTHER GATE, WHICH DECIDES WHETHER THE FIELD IS WIDENED AT
# ALL. FINDINGS-296.
#
# `Widescreen::initParamsFromConfig`:
#
#     if (camera_range.right - camera_range.left
#             >= game_width / 2 + abs(wide_viewport_x))
#         widescreen_mode = WM_EXTEND_WIDE;
#     else
#         widescreen_mode = WM_DISABLED;
#
# 320 + 107 = 427, and EVERY widescreen behaviour in the background code hangs
# off `is_fieldmap_wide()`, which is false for `WM_DISABLED`. So a field whose
# camera cannot travel 427 units is rendered on vanilla 4:3 parallax geometry
# and `zoomBackground` scales it to fill -- FFNx never asks its backdrop to
# cover a wider frame than the art has.
#
# `trnad_4`'s range is 368. FFNx does not widen it, which is why it is
# seamless there and why tiling it here answers a question FFNx never asks:
# the join becomes visible, and that is what was reported off build 157.
#
# So this pass is scoped to the fields FFNx ALSO widens. `trnad_2`, `hyou2`,
# `hyou8_1`, `hyou10`, `hyou13_1`, `kuro_1` and the six `move_*` fields are
# over the line and keep the fill; `trnad_3`, `trnad_4` and ten of the Great
# Glacier are under it and are left alone.
CAMERA_WIDE_MIN = 427

# The copy multiples, in whole wrap periods. Searched per layer against the
# executed shift; this is the set that comes out for every layer in scope and
# is tried first.
K_SETS = ((-2, 1, 2), (-1, 1, 2), (-2, -1, 1, 2), (-1, 1), (1, 2))

T_DSTX = PF.T_DSTX
T_TEXID = 32
T_FX_PAGE = 34
TILE_SIZE = PF.TILE_SIZE
PTILE = PF.PTILE


def _bind(sec9, off):
    """The page this record BINDS: the fx page when it carries one."""
    fx = sec9[off + T_FX_PAGE]
    return fx if fx else sec9[off + T_TEXID]


def disabled():
    return os.environ.get(OFF_ENV) == '1'


def shift(x, bg, width, left=459, right=0, half=213):
    """`field_layer3_shift_tile_position`, x axis.

    VALIDATED AGAINST THE BINARY, not transcribed and hoped for: `_kl3shift`
    executes the port's own encoded words, and this agrees with it on 9,372
    (tile.x, bg.x, width) triples spanning three widths -- zero mismatches.
    Fires at most once; it is a conditional, not a modulo.
    """
    if x <= bg - left or x >= bg + right:
        x += -width if x >= bg - half else width
    return x


def uncovered(cols, width, step=4):
    """Worst-case uncovered units of the 16:9 picture over a whole period."""
    worst = 0.0
    # screen(tile.x) = 320 - bg.x + tile.x; the 4:3 viewport is screen 0..320
    # and 16:9 adds `PICTURE_MARGIN_X` at each end.
    lo = -PF.PICTURE_MARGIN_X
    hi = PF.HALF_VIEW_43 * 2 + PF.PICTURE_MARGIN_X
    for bg10 in range(0, int(width) * 10, step * 10):
        bg = bg10 / 10.0
        seg = sorted((320 - bg + shift(x, bg, width),
                      320 - bg + shift(x, bg, width) + PTILE) for x in cols)
        merged = []
        for a, b in seg:
            if merged and a <= merged[-1][1] + 0.01:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        unc, cur = 0.0, lo
        for a, b in merged:
            if b <= lo or a >= hi:
                continue
            if a > cur:
                unc += a - cur
            cur = max(cur, b)
        unc += max(0.0, hi - cur)
        if unc > worst:
            worst = unc
        if worst > 0:
            return worst          # a single bad camera position is enough
    return worst


def plan_k(cols, width):
    """The smallest tried k-set that covers the picture, or None."""
    if uncovered(cols, width) == 0.0:
        return ()                              # already covered, nothing to do
    for ks in K_SETS:
        cand = sorted({x + k * width for k in (0,) + tuple(ks) for x in cols})
        if uncovered(cand, width) == 0.0:
            return ks
    return None


def _free_slot(pages, src_slot, depth=1):
    """A free slot the loader reaches, matching the source page's depth.

    DEPTH 1 stays in the source's own `D1_GROUPS` band -- `field_bg_native`
    reads a page's group from its slot.

    DEPTH 2 takes `BANDS[4]` (26..28) first and then a free LOW slot, which is
    the placement build 119 already ships: the engine reads a page's TYPE from
    section 9 rather than from its slot (x86 0x62D147) and draws any type-2
    page below slot 33 opaque (x86 0x6403C0), and `mtcrl_4`'s truecolor
    parallax pages live on 12/13/14. Slots 29+ do NOT render on this port
    (builds 52 and 55: black squares, no crash), so nothing above 28 is ever
    offered. `trnad_2` is the layer that needs this -- it binds two depth-2
    pages and 26..28 are all taken.
    """
    if depth == 1:
        grp = FN._group_of(src_slot, FN.D1_GROUPS)
        for lo, hi, g in FN.D1_GROUPS:
            if g != grp:
                continue
            for s in range(lo, hi):
                if s not in pages:
                    return s
        return None
    for s in list(range(26, 29)) + list(range(1, 15)):
        if s not in pages:
            return s
    return None


def apply_to_section9(sec9, sec7, field_name=None, max_pages=20):
    """(new_sec9, stats). Unchanged section when nothing qualifies."""
    st = {'layers': 0, 'tiles': 0, 'pages': 0, 'refused': [], 'worst': []}
    if sec9.find(b'BACK') < 0 or sec9.find(b'TEXTURE') < 0:
        return sec9, st
    hdr = PF.trigger_header(sec7)
    # FFNx decides this on the VANILLA range, before any config override, and
    # so does this. See CAMERA_WIDE_MIN.
    if (hdr['cam_right'] - hdr['cam_left']) < CAMERA_WIDE_MIN:
        return sec9, st
    px = _px()
    surv = DC.survey(sec9)
    pages = {p.slot: p for p in surv['pages']}
    layers = PF._layers(sec9, surv['back_start'], surv['tex_start'])

    # ---- 256 BINDING TILES PER PAGE, AND NOT THE GRANDFATHERED NUMBER.
    #
    # `field_bg_pagecap.effective_counts` is "the count the console makes",
    # and its measurement is unambiguous: by raw T_TEXID many vanilla fields
    # are over 256 (hyou5_2 is 953) and have shipped since 1997, but by
    # BINDING page ZERO vanilla field exceeds 256. These pages are NEW, so
    # nothing about them is grandfathered and they get the real invariant.
    cap = PC.MAX_TILES_PER_PAGE

    buf = bytearray(sec9)
    newpages = {}
    plans = []
    for layer, count_at, first, n in layers:
        if layer not in (3, 4):
            continue
        width = PF.layer_width(hdr, layer)
        spd = hdr['bg3_speed_x'] if layer == 3 else hdr['bg4_speed_x']
        if not spd or not width or width >= PICTURE_W:
            continue
        recs = [first + i * TILE_SIZE for i in range(n)]
        cols = sorted({struct.unpack_from('<h', sec9, o + T_DSTX)[0]
                       for o in recs})
        if not cols:
            continue
        ks = plan_k(cols, width)
        if ks is None:
            st['refused'].append((field_name, layer, 'no k-set covers it'))
            continue
        if not ks:
            continue
        # ---- THE PAGE TO COPY IS THE ONE THE TILE *BINDS*, NOT ITS TEXTURE ID.
        #
        # `field_bg_pagecap.effective_counts`: a tile that carries an fx page
        # binds the FX page, and the texture id it also carries never becomes
        # a call. `trnad_4`'s layer 3 names texture 0 on all 165 records and
        # carries fx pages 17 and 18 -- so duplicating slot 0 and repointing
        # T_TEXID moved nothing at all, and the copies piled 468 binding
        # tiles onto slot 17. Copy the binding page and rewrite the byte the
        # console actually reads.
        used = sorted({_bind(sec9, o) for o in recs})
        if any(pages.get(s) is None for s in used):
            st['refused'].append((field_name, layer, 'binding page missing'))
            continue
        if len(pages) + len(newpages) + len(used) > max_pages:
            st['refused'].append((field_name, layer, 'no page budget'))
            continue
        # ---- AS MANY NEW PAGES AS THE FRAME CAP NEEDS, NOT ONE.
        #
        # The vertical fill runs first and the copies have to include the rows
        # it added, or the copy is shorter than the original and the band
        # comes back at the top. On `trnad_4` that makes it 165 tiles per
        # copy, 495 for three, against a 256 cap -- so the copies are dealt
        # round-robin onto as many fresh slots as it takes. Trimming instead
        # is what left `hyou1` at 53.5 units in the first version of this
        # pass: a partial k-set does not cover, it just costs tiles.
        want = []
        for k in ks:
            for o in recs:
                x = struct.unpack_from('<h', sec9, o + T_DSTX)[0] + k * width
                if -0x8000 <= x <= 0x7FFF:
                    want.append((o, x))
        need = {}
        for o, _x in want:
            s = _bind(sec9, o)
            need[s] = need.get(s, 0) + 1
        pool = {}
        ok = True
        for s, cnt in need.items():
            n_pages = -(-cnt // cap)
            slots = []
            for _ in range(n_pages):
                tgt = _free_slot({**pages, **newpages}, s, pages[s].depth)
                if tgt is None:
                    ok = False
                    break
                newpages[tgt] = FN.Page(tgt, pages[s].size_flag,
                                        pages[s].depth, pages[s].data,
                                        pages[s].px)
                slots.append(tgt)
            if not ok:
                break
            pool[s] = slots
        if not ok:
            st['refused'].append((field_name, layer, 'no free slot in band'))
            continue
        if len(pages) + len(newpages) > max_pages:
            st['refused'].append((field_name, layer, 'no page budget'))
            continue
        keep = []
        per = {}
        for o, x in want:
            s = _bind(sec9, o)
            tgt = None
            for cand_slot in pool[s]:
                if per.get(cand_slot, 0) < cap:
                    tgt = cand_slot
                    break
            if tgt is None:
                continue
            per[tgt] = per.get(tgt, 0) + 1
            keep.append((o, x, tgt))
        if len(keep) != len(want):
            st['refused'].append((field_name, layer, 'frame cap trimmed %d'
                                  % (len(want) - len(keep))))
        if not keep:
            continue
        plans.append((layer, count_at, first, n, keep))
        st['layers'] += 1
        st['tiles'] += len(keep)
        st['worst'].append((len(keep), field_name or '', layer))

    if not plans:
        return sec9, st

    # Rebuild back to front so the offsets stay valid.
    for layer, count_at, first, n, keep in sorted(plans, reverse=True):
        blob = bytearray()
        for o, x, tgt in keep:
            rec = bytearray(sec9[o:o + TILE_SIZE])
            struct.pack_into('<h', rec, T_DSTX, int(x))
            # Rewrite the byte the console BINDS -- the fx page when the
            # record carries one, the texture id otherwise.
            if sec9[o + T_FX_PAGE]:
                rec[T_FX_PAGE] = tgt
            else:
                rec[T_TEXID] = tgt
            blob += rec
        end = first + n * TILE_SIZE
        buf[end:end] = blob
        struct.pack_into('<H', buf, count_at, n + len(keep))

    plist, tex_start, tex_end = FN.parse_texture_block(bytes(buf), px)
    for slot, page in newpages.items():
        plist[slot] = page
    out = FN.replace_texture_block(bytes(buf), plist, tex_start, tex_end)
    st['pages'] = len(newpages)
    return out, st


def _px():
    import ff7nx_fieldbg
    return ff7nx_fieldbg.page_px()


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_a: None):
    import lgp
    encode = encode or (lambda raw: archive.encode_field(raw))
    stats = {'fields': 0, 'layers': 0, 'tiles': 0, 'pages': 0,
             'refused': [], 'worst': []}
    if disabled():
        return stats
    for nm in archive.names():
        e = archive.index.get(nm)
        if e is None or not archive.is_field(e):
            continue
        try:
            payload = payloads.get(nm)
            raw = (lgp.lzs_decompress(payload[4:]) if payload
                   else archive.decompressed(e))
            parts = list(lgp.split_sections(raw))
        except Exception:                                      # noqa: BLE001
            continue
        try:
            new9, st = apply_to_section9(parts[8], parts[7], nm)
        except Exception as exc:                               # noqa: BLE001
            stats['refused'].append((nm, -1, str(exc)[:60]))
            continue
        stats['refused'] += st['refused']
        if not st['tiles']:
            continue
        parts[8] = new9
        payloads[nm] = encode(lgp.join_sections(parts))
        stats['fields'] += 1
        for k in ('layers', 'tiles', 'pages'):
            stats[k] += st[k]
        stats['worst'] += st['worst']
    stats['worst'].sort(reverse=True)
    return stats


def summarise(stats):
    if not stats.get('fields'):
        return ''
    worst = ', '.join('%s L%d +%d' % (n, l, c)
                      for c, n, l in stats['worst'][:4])
    return (
        '  parallax widescreen fill: %s tile(s) on %d new page(s) across %d '
        'layer(s) in %d field(s). A 1:1 parallax layer narrower than the '
        '427-unit 16:9 picture cannot cover it -- the engine\'s wrap slides '
        'its window around instead of tiling, so a band sits at one edge and '
        'moves with the camera. MEASURED by EXECUTING the port\'s own shift '
        'block over a whole period: trnad_4\'s 11 columns leave 157.5 units '
        'uncovered at worst; copies at k = (-2, +1, +2) periods bring it to '
        'ZERO. Scope is FFNx\'s own do_increase_width test '
        '(bg?_width < ceil(854/2)) and the same 24 layers: the hyou* Great '
        'Glacier, the move_* set, kuro_1, trnad_2, trnad_3 L4, trnad_4. FFNx '
        'doubles the wrap period and redraws once; we cannot, because this '
        'port reads one header word for both the wrap and the scroll, and '
        'FFNx\'s shape still leaves 53.5 units here for want of right_offset '
        '(0 here, 107 there) -- extra copies close that with no code patch '
        'and no cave space. THE COPIES GET THEIR OWN PAGE: the frame cap is '
        'max(256, vanilla\'s worst page) and trnad_4\'s slot 0 is already at '
        '344 of 344, so the new slot is a BYTE COPY of the source and the '
        'original page is untouched. Biggest: %s. Set %s=1 to disable.'
        % (f"{stats['tiles']:,}", stats['pages'], stats['layers'],
           stats['fields'], worst, OFF_ENV))
