#!/usr/bin/env python3
"""ff7nx_bandbake.py -- bake additive overlays into layer 1's holes. BUILD 618z.

whitein, hardware 10-04 (build 386): "weird lines on the textures" -- thin
dark stair-step lines across the glass dome, along the purple ribbon band.

CAUSE (measured on the video: the line is 77% of the light either side, one
pixel wide, and follows layer 1's hole exactly). In the 1997 art the glass
inside the ribbon band is not on layer 1: layer 1 is BLACK there, with the
band's 1x stair-step outline, and a static ADDITIVE overlay on layer 2
paints that glass on top (black + overlay = overlay). In the build the
overlay is Cosmos HD art on truecolor pages 19-21. The port filters layer 1,
so the black bleeds half a texel into the glass, and alpha-tests the keyed
overlay, so its edge pulls in by half a texel. Both happen on the same
stair edge: a thin dark line. A software render with hard texels is clean,
which is why it never showed up off hardware.

FIX (FIELDS only; static records, truecolor pages): wherever layer 1 is
black (0 or the 8/255 lift 0x0841) under static additive overlay
records, their summed colour is written into layer 1 and their texels
there are cleared. Black + overlay = overlay, so the picture is
the same, but the band is now opaque layer-1 art continuous with the glass
beside it: nothing to bleed and no keyed edge to pull in. Only exclusive
cells (no other record samples them) are touched.

SEVENTH_NX_NO_BAND_BAKE=1 disables.
"""
from __future__ import annotations

import collections
import os
import struct

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_BAND_BAKE'
# Scoped: a dry run over build 386 finds 114 fields with black layer 1
# under a static additive overlay (2.2M texels: junone5, games_2, seto1,
# ...). In most of them the overlay is a light IN FRONT of the characters
# (its z), and baking it into layer 1 would put it behind them. whitein's
# band is above the walkmesh; ujun_w shares its background.
FIELDS = ('whitein', 'ujun_w')
GROW = 2                 # texels (2/3 of a field unit at 768px pages)
RING = 2                 # overlay texels next to layer 1's art
MIN_FILL = 24            # texels per field below which nothing is written


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _black565(v):
    v = v.astype(np.uint32)
    return ((v >> 11) <= 1) & (((v >> 5) & 63) <= 2) & ((v & 31) <= 1)


def plan_field(parts):
    """(new section 9, baked texel count) -- raises when nothing to do."""
    sec9, _np = _promote_fillers(parts, parts[8])
    return _ring(parts, sec9, 0)


FX_BAND = (15, 26)       # truecolor pages kept for FX (608b gate)
UV_CELL = 625000         # srcXBig / srcYBig per 16-texel cell
TILE = 52


def _promote_fillers(parts, sec9):
    """BUILD 618z5. whitein, hardware 10-05: "weird horizontal lines where
    these texture regions meet ... they get thinner or thicker depending on
    where i am on the screen" -- the middle of the central spire.

    The bake below only sees layer-1 cells on TRUECOLOR pages. Under the
    spire, at y = -32, layer 1 is a black/clear 1x filler cell on paletted
    page 1 SHARED by several records, so that row could not be baked while
    the rows above (y = -48) and below (y = -16) were. A baked row meeting
    an additive row on a tile edge is exactly the one-pixel filtered seam
    the bake exists to remove (the overlay's keyed edge pulls in by half a
    texel; its width follows the camera's sub-pixel position), and the
    all-or-nothing patch rule could not catch it because the filler row
    was never part of the hole map.

    FIX: a layer-1 record on a paletted page whose cell is all black or
    clear (colour 0), inside 4:3, under a static additive truecolor overlay,
    gets its own cell on a truecolor page: clear stays 0x0000 (still not
    drawn), black becomes 0x0841 (the 8/255 lift, graded to 0). Same
    picture; the bake then treats it like every other black hole.
    Returns (section 9, records promoted). Never raises."""
    try:
        import diag_common as DC
        import field_bg_native as FN
        import ff7nx_marginblack as MB
        pl, ts, _te, px = DC.parse_pages(sec9)
        pm = {p.slot: p for p in pl if p is not None}
        k = px // 16
        cols, _h, npg, cpp = MB.palette_colours(parts[3])
        pal = cols.astype(np.int64).reshape(npg, cpp)
        used = collections.defaultdict(set)
        binds = collections.Counter()
        under = set()
        l1 = []
        for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
            for o in offs:
                bl = sec9[o + 28]
                for pg, so in ((sec9[o + 32], 10),) + (
                        ((sec9[o + 34], 14),) if bl else ()):
                    sx, sy = struct.unpack_from('<hh', sec9, o + so)
                    used[pg].add((sx // 16, sy // 16))
                    binds[pg] += 1
                x, y = struct.unpack_from('<hh', sec9, o + 2)
                if (layer == 2 and bl and not sec9[o + 26]
                        and sec9[o + 30] in (1, 3)):
                    p = pm.get(sec9[o + 34])
                    if p is None or p.depth != 2 or p.size_flag:
                        continue
                    sx, sy = struct.unpack_from('<hh', sec9, o + 14)
                    if np.frombuffer(p.data, '<u2').reshape(px, px)[
                            sy // 16 * k:sy // 16 * k + k,
                            sx // 16 * k:sx // 16 * k + k].any():
                        under.add((x, y))
                elif layer == 1 and not bl and not sec9[o + 26]:
                    l1.append(o)
        plan = []
        for o in l1:
            x, y = struct.unpack_from('<hh', sec9, o + 2)
            p = pm.get(sec9[o + 32])
            if (x, y) not in under or p is None or p.depth != 1 \
                    or not (-160 <= x < 160):
                continue
            sx, sy = struct.unpack_from('<hh', sec9, o + 10)
            q = sec9[o + 22]
            if q >= len(pal):
                continue
            a = np.frombuffer(p.data, np.uint8, count=65536).reshape(
                256, 256)[sy:sy + 16, sx:sx + 16]
            c = pal[q][a] & 0x7FFF
            if (((c & 31) > 1) | (((c >> 5) & 31) > 1)
                    | (((c >> 10) & 31) > 1)).any():
                continue                   # real art, not a filler
            v = np.where(np.repeat(np.repeat(c == 0, k // 16, 0),
                                   k // 16, 1), 0, 0x0841).astype(np.uint16)
            plan.append((o, v))
        if not plan:
            return sec9, 0
        slot = None
        for s_ in sorted(pm, key=lambda s: len(used[s])):
            p = pm[s_]
            if p.depth != 2 or p.px != px or p.size_flag \
                    or FX_BAND[0] <= s_ < FX_BAND[1]:
                continue
            fr = [(cx, cy) for cy in range(16) for cx in range(16)
                  if (cx, cy) not in used[s_]]
            if len(fr) >= len(plan) and binds[s_] + len(plan) <= 256:
                slot = s_
                break
        if slot is None:
            return sec9, 0
        d = np.frombuffer(pm[slot].data, '<u2').reshape(px, px).copy()
        buf = bytearray(sec9)
        for (o, v), (cx, cy) in zip(plan, fr):
            d[cy * k:(cy + 1) * k, cx * k:(cx + 1) * k] = v
            buf[o + 32] = slot
            struct.pack_into('<hh', buf, o + 10, cx * 16, cy * 16)
            struct.pack_into('<II', buf, o + 42, cx * UV_CELL, cy * UV_CELL)
        sec9 = bytes(buf)
        plist, t0, t1 = FN.parse_texture_block(sec9, px)
        q_ = plist[slot]
        plist[slot] = FN.Page(slot, q_.size_flag, 2, d.tobytes(), q_.px)
        return FN.replace_texture_block(sec9, plist, t0, t1), len(plan)
    except Exception:                                          # noqa: BLE001
        return sec9, 0


def _ring(parts, sec9, done):
    """The ADDITIVE case (whitein's glass band). Layer 1 is black where a
    static additive overlay (Cosmos HD) paints the glass on top. The port
    filters layer 1, so its black hole bleeds half a texel into the glass,
    and alpha-tests the keyed overlay, so the overlay's edge pulls in by
    half a texel: a thin stair line at 77% of the light either side
    (hardware 10-04, measured on the video).

    Black + additive = the overlay's colour, so the overlay is BAKED into
    layer 1 wherever layer 1 is black under it, and the overlay's texels
    there are cleared. The band is then one opaque layer, continuous with
    the glass beside it -- nothing to bleed and nothing to pull in. Only
    texels drawn by one exclusive overlay record over an exclusive layer-1
    cell, truecolor pages only. Everything else is untouched."""
    from scipy import ndimage as ND
    import diag_common as DC
    import field_bg_native as FN
    pl, ts, _te, px = DC.parse_pages(sec9)
    pm = {p.slot: p for p in pl if p is not None}
    k = px // 16
    uses = collections.Counter()
    l1, l2, add = [], [], []
    for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in offs:
            bl = sec9[o + 28]
            pg = sec9[o + 34] if bl else sec9[o + 32]
            sx, sy = struct.unpack_from('<hh', sec9, o + (14 if bl else 10))
            uses[(pg, sx // 16, sy // 16)] += 1
            p = pm.get(pg)
            if p is None or p.size_flag or sec9[o + 26] or p.depth != 2:
                continue
            x, y = struct.unpack_from('<hh', sec9, o + 2)
            if layer == 1 and not bl:
                l1.append((pg, sx // 16, sy // 16, x, y))
            elif layer == 2 and not bl:
                l2.append((pg, sx // 16, sy // 16, x, y))
            elif layer == 2 and bl and sec9[o + 30] in (1, 3):
                add.append((pg, sx // 16, sy // 16, x, y))
    if not l1 or not add:
        if done:
            return sec9, done
        raise ValueError('nothing under layer 2')
    allr = l1 + add
    x0 = min(r[3] for r in allr)
    y0 = min(r[4] for r in allr)
    W = (max(r[3] for r in allr) + 16 - x0) * k // 16
    H = (max(r[4] for r in allr) + 16 - y0) * k // 16
    pages = {s_: np.frombuffer(pm[s_].data, '<u2').reshape(px, px)
             for s_ in {r[0] for r in allr + l2}}
    canvas = np.zeros((H, W), np.uint16)
    have = np.zeros((H, W), bool)
    for pg, cx, cy, x, y in l1:
        X, Y = (x - x0) * k // 16, (y - y0) * k // 16
        canvas[Y:Y + k, X:X + k] = pages[pg][cy * k:(cy + 1) * k,
                                             cx * k:(cx + 1) * k]
        have[Y:Y + k, X:X + k] = True
    asum = np.zeros((H, W, 3), np.float32)
    abad = np.zeros((H, W), bool)
    for pg, cx, cy, x, y in add:
        X, Y = (x - x0) * k // 16, (y - y0) * k // 16
        v = pages[pg][cy * k:(cy + 1) * k, cx * k:(cx + 1) * k].astype(
            np.uint32)
        asum[Y:Y + k, X:X + k] += np.stack(
            [((v >> 11) & 31) << 3, ((v >> 6) & 31) << 3, (v & 31) << 3],
            -1).astype(np.float32)
        if uses[(pg, cx, cy)] != 1:
            abad[Y:Y + k, X:X + k] = True
    l1ok = np.zeros((H, W), bool)
    for pg, cx, cy, x, y in l1:
        if uses[(pg, cx, cy)] == 1:
            X, Y = (x - x0) * k // 16, (y - y0) * k // 16
            l1ok[Y:Y + k, X:X + k] = True
    over = np.zeros((H, W), bool)          # layer 2's own opaque art
    for pg, cx, cy, x, y in l2:
        X, Y = (x - x0) * k // 16, (y - y0) * k // 16
        if X < 0 or Y < 0 or X + k > W or Y + k > H:
            continue
        over[Y:Y + k, X:X + k] |= pages[pg][cy * k:(cy + 1) * k,
                                            cx * k:(cx + 1) * k] != 0
    hole = _black565(canvas) & have
    # a HOLE is a solid black area (the 1997 band cut-out). Scattered black
    # texels in Cosmos's dark margin sky are not one: baked one by one they
    # left rows of dark specks in the ribbon on hardware (filtering mixes
    # each baked texel with its dark neighbours and each cleared overlay
    # texel with its lit ones -- hardware 10-05, the ribbon's left loop).
    # Keep only texels within 3 of a core that a 7x7 opening keeps.
    core = ND.binary_opening(hole, np.ones((7, 7), bool))
    hole &= ND.binary_dilation(core, np.ones((3, 3), bool), iterations=3)
    # and only inside the 4:3 picture: the cut-out is 1997 layer 1's; the
    # margins are Cosmos's own art, where a black block is a filler and
    # baking it draws a new edge against the dark sky beside it
    gx = x0 + np.arange(W) * 16.0 / k
    hole &= ((gx >= -160) & (gx < 160))[None, :]
    # where layer 2's opaque art sits between them the overlay lands on IT,
    # not on layer 1: leave those texels alone (whitein's spire tips)
    lit = hole & (asum.max(-1) > 0)
    ok = ~abad & l1ok
    near_over = ND.binary_dilation(over, np.ones((3, 3), bool), iterations=2)
    # all or nothing per connected patch of overlay-on-black: a patch baked
    # on one side of a tile edge and left additive on the other draws a new
    # one-pixel seam there (hardware 10-05: a dark vertical line in the
    # ribbon at x = -96, where a non-exclusive cell stopped the bake)
    lab, nl = ND.label(lit, np.ones((3, 3), bool))
    if nl:
        badl = np.unique(lab[lit & ~ok])
        bake = lit & ~np.isin(lab, badl[badl > 0])
    else:
        bake = lit & ok
    # next to layer 2's opaque art the overlay lands on IT, not on layer 1;
    # the seam this leaves sits against that opaque art
    bake &= ~near_over
    # BUILD 618z7 (hardware 10-05: "ugly / dirty looking pixellation on the
    # staircase", a dotted ring on the ribbon's inner edge at x -160..-156,
    # y -86..-82). The 4:3 cut above can split ONE piece of overlay-on-black
    # in two: the part inside 4:3 is baked, the part outside stays additive,
    # and the baked island's whole outline becomes the filtered seam the
    # bake exists to remove. A baked patch whose piece of overlay-on-black
    # continues, unbaked, outside 4:3 is therefore left additive, like the
    # rest of its piece. (whitein: one patch, 115 texels; the piece is 483,
    # 357 of them outside 4:3.)
    raw = _black565(canvas) & have & (asum.max(-1) > 0)
    rl, nr = ND.label(raw, np.ones((3, 3), bool))
    if nr and bake.any():
        out43 = ~((gx >= -160) & (gx < 160))[None, :]
        split = np.unique(rl[raw & ~bake & out43])
        split = split[split > 0]
        if len(split):
            bl, nb = ND.label(bake, np.ones((3, 3), bool))
            drop = np.unique(bl[bake & np.isin(rl, split)])
            bake &= ~np.isin(bl, drop[drop > 0])
    if not bake.any():
        if done:
            return sec9, done
        raise ValueError('nothing under layer 2')
    q = np.clip(np.floor(asum / 8.0 + 0.5), 0, 31).astype(np.uint32)
    enc = (q[..., 0] << 11 | (q[..., 1] << 1) << 5 | q[..., 2]).astype(
        np.uint16)                                 # green LSB clear (1555)
    data = {}
    n = 0
    for pg, cx, cy, x, y in l1:
        X, Y = (x - x0) * k // 16, (y - y0) * k // 16
        b = bake[Y:Y + k, X:X + k]
        if uses[(pg, cx, cy)] != 1 or not b.any():
            continue
        if pg not in data:
            data[pg] = pages[pg].copy()
        out = data[pg][cy * k:(cy + 1) * k, cx * k:(cx + 1) * k]
        out[b] = enc[Y:Y + k, X:X + k][b]
        n += int(b.sum())
    for pg, cx, cy, x, y in add:
        X, Y = (x - x0) * k // 16, (y - y0) * k // 16
        b = bake[Y:Y + k, X:X + k]
        if not b.any():
            continue
        if pg not in data:
            data[pg] = pages[pg].copy()
        data[pg][cy * k:(cy + 1) * k, cx * k:(cx + 1) * k][b] = 0
    if done + n < MIN_FILL:
        raise ValueError('nothing under layer 2 (%d texels)' % (done + n))
    if n:
        plist, t0, t1 = FN.parse_texture_block(sec9, px)
        for s_, d in data.items():
            q = plist[s_]
            plist[s_] = FN.Page(s_, q.size_flag, 2,
                                d.astype('<u2').tobytes(), px)
        sec9 = FN.replace_texture_block(sec9, plist, t0, t1)
    return sec9, done + n


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None,
                    only=None):
    import lgp
    st = {'names': [], 'texels': 0}
    if disabled():
        return st
    encode = encode or archive.encode_field
    for name in (only or FIELDS):
        entry = archive.index.get(name)
        try:
            if entry is None or not archive.is_field(entry):
                continue
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            if parts[8].find(b'BACK') < 0:
                continue
            parts[8], n = plan_field(parts)
        except Exception:                                      # noqa: BLE001
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        st['names'].append('%s:%d' % (name, n))
        st['texels'] += n
    return st


def summarise(st):
    if not st.get('names'):
        return ''
    return ('  BAND BAKE (BUILD 618z): %d texel(s) of static additive overlay baked '
            'into layer 1\'s black hole under it in %d field(s) (%s). %s=1 '
            'disables.' % (st['texels'], len(st['names']),
                           ', '.join(st['names'][:25])
                           + (' ...' if len(st['names']) > 25 else ''),
                           OFF_ENV))
