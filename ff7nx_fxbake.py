#!/usr/bin/env python3
"""Withdrawn build-160 experiment; retained default-off for reproducibility.

The experiment baked a margin FX record into the base cell below it. Its
diagnosis and implementation both read ``src_x/src_y`` (offsets 10/12) as the
FX source. That is the BASE source. FX uses ``src_x2/src_y2`` (14/16), and
after packing the canonical UV is at 42/46. In ``sinbil_1`` all affected base
sources are (0,0), while the FX sources are distinct. The bake therefore
repeated one bright cell into a rectangle.

The screenshot compositor made the same coordinate mistake, so its claimed
proof of an engine cull is invalid too. The real defect is data: palette 8's
Cosmos DDS leaves 24 margin cells blank while the same page's unambiguous
palette-0 DDS contains the complete gradient. ``ff7nx_fxart`` restores those
cells without baking, deleting records, or patching the executable.
"""
from __future__ import annotations

import os
import struct

import numpy as np

import diag_common as DC
import field_bg_dense as FD
import field_bg_native as FN
import field_bg_pagecap as PC

# ---- DEFAULT OFF. BUILD 160 SHIPPED THIS ON AND IT WAS A REGRESSION.
#
# On hardware it put a hard-edged, over-bright teal RECTANGLE in sinbil_1's
# upper-left corner -- worse than the boundary it was meant to remove.
#
# WHY THE GATE PASSED IT ANYWAY, which is the part worth keeping:
# `_kfxbake` checked two things, and both were true -- nothing inside the 4:3
# picture moved, and the step AT THE 4:3 EDGE fell from +34.7 to +0.3. Neither
# says anything about what the margin looks like INTERNALLY. A block of cells
# saturated to a uniform bright teal has a tiny edge step and is obviously
# wrong to look at.
#
# The repeated rectangle came from reading src1=(0,0) for every FX record,
# not from a measured blend coefficient. The compositor used the same wrong
# coordinate, so neither the blend claim nor the engine-cull claim survives.
# `SEVENTH_NX_FX_BAKE=1` re-enables the old code only for reproduction.
ON_ENV = 'SEVENTH_NX_FX_BAKE'
OFF_ENV = 'SEVENTH_NX_NO_FX_BAKE'

TILE_SIZE = 52
T_DSTX = DC.TILE_DST_X
T_DSTY = DC.TILE_DST_Y
T_SRCX = 10
T_SRCY = 12
T_PAL = 22
T_TEXID = 32
T_FX_PAGE = 34

# The 4:3 picture in tile-x: `screen(tile.x) = tile.x + 160`, viewport 0..320.
PIC_LO = -160
PIC_HI = 160


def disabled():
    if os.environ.get(OFF_ENV) == '1':
        return True
    return os.environ.get(ON_ENV) != '1'


def _cell(page, sx, sy):
    grid = FD.BIG_GRID if page.size_flag else FD.GRID
    return PC._cell_slice(page, sx, sy, grid)


def _fx_rgb565(page, sx, sy, pal, palettes):
    """The FX cell as R5G6B5, whatever depth it is stored at."""
    ys, xs = _cell(page, sx, sy)
    if page.depth == 2:
        arr = np.frombuffer(page.data, '<u2').reshape(page.px, page.px)
        return arr[ys, xs]
    arr = np.frombuffer(page.data, np.uint8).reshape(page.px, page.px)
    idx = arr[ys, xs]
    pal = min(pal, len(palettes) - 1)
    v = palettes[pal][idx].astype(np.int64)          # section 3 is BGR555
    r = (v & 31)
    g = ((v >> 5) & 31)
    b = ((v >> 10) & 31)
    # 555 -> 565: the green channel doubles, which is what every other
    # decode in this project does (`_pal_distance`, `hue_broken`).
    return ((r << 11) | (g * 2 << 5) | b).astype('<u2')


def _add565(dst, src):
    """Saturating additive blend, channel by channel, in R5G6B5."""
    d = dst.astype(np.int32)
    s = src.astype(np.int32)
    r = np.minimum(((d >> 11) & 31) + ((s >> 11) & 31), 31)
    g = np.minimum(((d >> 5) & 63) + ((s >> 5) & 63), 63)
    b = np.minimum((d & 31) + (s & 31), 31)
    return ((r << 11) | (g << 5) | b).astype('<u2')


def _console(sec9, cols, px):
    """What the console draws: base everywhere, FX only inside the 4:3 frame.

    The behaviour is not assumed -- it is what the hardware screenshot says,
    see the module docstring.
    """
    X0, Y0, W, H = -224, -120, 448, 240
    surv = DC.survey(sec9)
    pg = {p.slot: p for p in surv['pages']}
    img = np.zeros((H, W, 3), np.int32)
    items = []
    for lay, offs in DC.walk_layers(sec9, surv['back_start'],
                                    surv['tex_start']):
        if lay > 2:
            continue
        for o in offs:
            items.append((lay, sec9[o + T_FX_PAGE] != 0, o))
    items.sort(key=lambda t: (t[0], t[1]))
    for _lay, isfx, o in items:
        dx0 = struct.unpack_from('<h', sec9, o + T_DSTX)[0]
        dy0 = struct.unpack_from('<h', sec9, o + T_DSTY)[0]
        if isfx and (dx0 < PIC_LO or dx0 >= PIC_HI):
            continue
        sl = sec9[o + T_FX_PAGE] or sec9[o + T_TEXID]
        p = pg.get(sl)
        if p is None:
            continue
        arr = np.frombuffer(p.data, np.uint8 if p.depth == 1 else '<u2'
                            ).reshape(p.px, p.px)
        try:
            ys, xs = _cell(p, sec9[o + T_SRCX], sec9[o + T_SRCY])
        except Exception:                                      # noqa: BLE001
            continue
        c = arr[ys, xs]
        f = max(1, c.shape[0] // 16)
        if p.depth == 1:
            pal = min(sec9[o + T_PAL], len(cols) - 1)
            v = cols[pal][c].astype(np.int64)
            rgb = np.stack([(v & 31) << 3, ((v >> 5) & 31) << 3,
                            ((v >> 10) & 31) << 3], -1)
        else:
            v = c.astype(np.int64)
            rgb = np.stack([((v >> 11) & 31) << 3, ((v >> 5) & 63) << 2,
                            (v & 31) << 3], -1)
        sm = rgb[::f, ::f][:16, :16]
        dx, dy = dx0 - X0, dy0 - Y0
        if dx < 0 or dy < 0 or dx + 16 > W or dy + 16 > H:
            continue
        if isfx:
            img[dy:dy + 16, dx:dx + 16] += sm
        else:
            img[dy:dy + 16, dx:dx + 16] = sm
    return np.clip(img, 0, 255)


def apply_to_section9(sec9, px=None, field_name=None):
    """(new_sec9, stats). Unchanged when no margin FX tile qualifies."""
    st = {'fields': 0, 'baked': 0, 'removed': 0, 'refused': 0,
          'reverted': 0}
    if disabled() or sec9.find(b'BACK') < 0:
        return sec9, st
    import ff7nx_marginblack as MB
    import ff7nx_parallaxfill as PF
    try:
        surv = DC.survey(sec9)
        # DERIVE px FROM THE SECTION, not from the settings. `page_px()` is 0
        # until the build bootstraps, and a wrong px makes
        # `parse_texture_block` raise -- which this pass would swallow as
        # "nothing to do". That is how the first run reported 0 of everything.
        if not px:
            d2 = [p.px for p in surv['pages'] if p.depth == 2]
            px = max(d2) if d2 else FN.VANILLA_PX
        pages_list, tex_start, tex_end = FN.parse_texture_block(sec9, px)
        pages = {p.slot: p for p in pages_list if p is not None}
        layers = PF._layers(sec9, surv['back_start'], surv['tex_start'])
        parts_pal = None
    except Exception:                                          # noqa: BLE001
        return sec9, st

    base = {}
    fxm = []
    uses = {}
    # ---- COVERAGE PER TEXEL, NOT PER (page, sx, sy). FINDINGS-302.
    #
    # `sx`/`sy` are arbitrary bytes, not multiples of the grid, so two tiles
    # can name DIFFERENT cells whose texel rectangles OVERLAP. Counting exact
    # keys called those unshared and editing one damaged the other -- that is
    # `md_e1` changing by 255 inside the 4:3 picture. Count the texels.
    cover = {}
    # REFERENCES ARE COUNTED OVER EVERY LAYER, 1..4.
    #
    # The first version counted only layers 1 and 2, so a base cell also named
    # by a parallax tile looked unshared and was edited -- which moved art
    # INSIDE the 4:3 picture on `ancnt2` and `md_e1`. The gate caught it.
    for layer, _c, first, n in layers:
        for i in range(n):
            o = first + i * TILE_SIZE
            dx = struct.unpack_from('<h', sec9, o + T_DSTX)[0]
            dy = struct.unpack_from('<h', sec9, o + T_DSTY)[0]
            if abs(dx) > 1000:
                continue
            binding = sec9[o + T_FX_PAGE] or sec9[o + T_TEXID]
            key = (binding, sec9[o + T_SRCX], sec9[o + T_SRCY])
            uses[key] = uses.get(key, 0) + 1
            _p = pages.get(binding)
            if _p is not None:
                if binding not in cover:
                    cover[binding] = np.zeros((_p.px, _p.px), np.int16)
                try:
                    _ys, _xs = _cell(_p, sec9[o + T_SRCX], sec9[o + T_SRCY])
                    cover[binding][_ys, _xs] += 1
                except Exception:                              # noqa: BLE001
                    pass
            if layer > 2:
                continue
            if sec9[o + T_FX_PAGE]:
                if dx < PIC_LO or dx >= PIC_HI:
                    fxm.append((dx, dy, o, layer))
            elif (dx, dy) not in base:
                base[(dx, dy)] = o
            else:
                base[(dx, dy)] = None      # two base tiles here: ambiguous
    if not fxm:
        return sec9, st

    arrays = {}
    drop = set()
    done_cells = set()
    for dx, dy, o, _layer in fxm:
        b = base.get((dx, dy))
        if b is None:
            st['refused'] += 1
            continue
        bp = pages.get(sec9[b + T_TEXID])
        fp = pages.get(sec9[o + T_FX_PAGE])
        if bp is None or fp is None or bp.depth != 2:
            st['refused'] += 1
            continue
        bkey = (sec9[b + T_TEXID], sec9[b + T_SRCX], sec9[b + T_SRCY])
        if bkey in done_cells:
            st['refused'] += 1        # never add light into a cell twice
            continue
        if uses.get(bkey, 0) != 1:
            st['refused'] += 1        # shared cell: editing it would move
            continue                  # art inside the 4:3 picture too
        try:
            _ys, _xs = _cell(bp, sec9[b + T_SRCX], sec9[b + T_SRCY])
            if int(cover[bp.slot][_ys, _xs].max()) != 1:
                st['refused'] += 1    # another tile's cell overlaps these
                continue              # texels; editing them would move it
        except Exception:                                      # noqa: BLE001
            st['refused'] += 1
            continue
        if bp.slot not in arrays:
            arrays[bp.slot] = np.frombuffer(
                bp.data, '<u2').reshape(bp.px, bp.px).copy()
        try:
            if parts_pal is None:
                parts_pal = ()
            fx = _fx_rgb565(fp, sec9[o + T_SRCX], sec9[o + T_SRCY],
                            sec9[o + T_PAL], _PALS.get(id(sec9), ()))
            ys, xs = _cell(bp, sec9[b + T_SRCX], sec9[b + T_SRCY])
            cur = arrays[bp.slot][ys, xs]
            if fx.shape != cur.shape:
                f = fx.shape[0]
                t = cur.shape[0]
                if t % f == 0:
                    k = t // f
                    fx = np.repeat(np.repeat(fx, k, 0), k, 1)
                elif f % t == 0:
                    k = f // t
                    fx = fx[::k, ::k][:t, :t]
                else:
                    st['refused'] += 1
                    continue
            arrays[bp.slot][ys, xs] = _add565(cur, fx)
        except Exception:                                      # noqa: BLE001
            st['refused'] += 1
            continue
        done_cells.add(bkey)
        drop.add(o)
        st['baked'] += 1

    if not drop:
        return sec9, st

    # ---- REMOVE THE FX RECORDS, back to front so offsets stay valid.
    buf = bytearray(sec9)
    # BACK TO FRONT BY FILE POSITION, not by layer number. Layer order in the
    # BACK block is not guaranteed to match layer numbering, and shrinking an
    # earlier block first invalidates every offset after it.
    for layer, count_at, first, n in sorted(layers, key=lambda t: -t[2]):
        end = first + n * TILE_SIZE
        keep = bytearray()
        kept = 0
        for i in range(n):
            o = first + i * TILE_SIZE
            if o in drop:
                continue
            keep += sec9[o:o + TILE_SIZE]
            kept += 1
        if kept == n:
            continue
        buf[first:end] = keep
        struct.pack_into('<H', buf, count_at, kept)
        st['removed'] += n - kept

    out = bytes(buf)
    pages_list2, ts2, te2 = FN.parse_texture_block(out, px)
    for slot, arr in arrays.items():
        p = pages[slot]
        pages_list2[slot] = FN.Page(slot, p.size_flag, 2, arr.tobytes(), p.px)
    out = FN.replace_texture_block(out, pages_list2, ts2, te2)

    # ---- VERIFY THE INVARIANT, AND REVERT THE FIELD IF IT IS BROKEN.
    #
    # This pass edits cells and deletes records, and four separate attempts to
    # reason about which cells were safe were each wrong on some field. So the
    # rule is checked rather than argued: render the CONSOLE model of the
    # section before and after, and if a single pixel INSIDE the 4:3 picture
    # moved, hand back the original untouched. A field that cannot be improved
    # safely is left exactly as it was.
    cols = _PALS.get(id(sec9))
    if cols is not None:
        try:
            a = _console(sec9, cols, px)
            b = _console(out, cols, px)
            edge = PIC_LO - (-224)
            # (1) nothing inside the 4:3 picture may move, at all.
            if int(np.abs(a[:, edge:] - b[:, edge:]).max()) != 0:
                st['reverted'] = st['baked']
                st['baked'] = st['removed'] = 0
                return sec9, st
            # (2) THE STEP MUST MOVE TOWARD ZERO, NEVER PAST IT.
            #
            # Baking light the console was already drawing, or stacking more
            # of it than the engine would, overshoots: `nivl_b2` went from a
            # +30 step to -56, a margin BRIGHTER than the picture. That is a
            # different defect, not a fix. Requiring |after| <= |before|
            # makes the pass non-worsening by construction, which is the
            # guarantee worth having when the model of the engine is
            # incomplete.
            band_a = a[0:67].mean(axis=(0, 2))
            band_b = b[0:67].mean(axis=(0, 2))
            s_a = abs(float(band_a[edge] - band_a[edge - 1]))
            s_b = abs(float(band_b[edge] - band_b[edge - 1]))
            if s_b > s_a + 0.5:
                st['reverted'] = st['baked']
                st['baked'] = st['removed'] = 0
                return sec9, st
        except Exception:                                      # noqa: BLE001
            st['reverted'] = st['baked']
            st['baked'] = st['removed'] = 0
            return sec9, st
    st['fields'] = 1
    return out, st


# The palette table for the field currently being processed. Set by
# `apply_to_flevel`, which is the only place section 3 is in scope.
_PALS = {}


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_a: None,
                    px=None):
    import lgp
    import ff7nx_marginblack as MB
    encode = encode or (lambda raw: archive.encode_field(raw))
    stats = {'fields': 0, 'baked': 0, 'removed': 0, 'refused': 0,
             'reverted': 0, 'worst': []}
    if disabled():
        return stats
    if not px:
        import ff7nx_fieldbg
        px = ff7nx_fieldbg.page_px() or None
    for nm in archive.names():
        e = archive.index.get(nm)
        if e is None or not archive.is_field(e):
            continue
        try:
            payload = payloads.get(nm)
            raw = (lgp.lzs_decompress(payload[4:]) if payload
                   else archive.decompressed(e))
            parts = list(lgp.split_sections(raw))
            cols, _h, _n, _c = MB.palette_colours(parts[3])
        except Exception:                                      # noqa: BLE001
            continue
        _PALS[id(parts[8])] = cols
        try:
            new9, st = apply_to_section9(parts[8], px, nm)
        except Exception:                                      # noqa: BLE001
            _PALS.pop(id(parts[8]), None)
            continue
        _PALS.pop(id(parts[8]), None)
        stats['refused'] += st['refused']
        stats['reverted'] += st.get('reverted', 0)
        if not st['baked']:
            continue
        parts[8] = new9
        payloads[nm] = encode(lgp.join_sections(parts))
        for k in ('fields', 'baked', 'removed'):
            stats[k] += st[k]
        stats['worst'].append((st['baked'], nm))
    stats['worst'].sort(reverse=True)
    return stats


def summarise(stats):
    if not stats.get('fields'):
        return ''
    worst = ', '.join('%s %d' % (n, c) for c, n in stats['worst'][:4])
    return (
        '  WITHDRAWN FX BAKE WAS EXPLICITLY ENABLED: %s tile(s) in %d field(s) '
        'were changed by the obsolete build-160 experiment. It reads the base '
        'source coordinate as the FX source and can repeat one cell into a '
        'rectangle; disable it with %s=1. Removed records: %s; refused: %s; '
        'biggest: %s.'
        % (f"{stats['baked']:,}", stats['fields'], OFF_ENV,
           f"{stats['removed']:,}", f"{stats['refused']:,}", worst))
