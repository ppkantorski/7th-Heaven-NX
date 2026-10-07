#!/usr/bin/env python3
"""ff7nx_fxtrial.py -- the last paletted additive FX page of a field made
truecolor under a per-field memory TRIAL ceiling above the proven 35 MB.

BUILD 618q. Hardware 10-03:
  * del3 (Costa del Sol beach): "a weird pixellated patch on the far right"
    of the surf.
  * las0_3: part of the rising mist looked dirty in half of its frames.

MEASURED: both fields convert all their additive FX pages to 768px
truecolor except ONE, which stays 1997 paletted art at 256px (1x) because
converting it would pass the 35.0 MB field ceiling:
  * del3:   FX pages 16-22 truecolor, page 23 (29 surf records on the right)
            paletted; 34.37 MB.
  * las0_3: FX pages 17-21 truecolor, page 22 (13 mist records per frame in
            frames 4-7, the bottom rows) paletted; 34.38 MB.
No near-duplicate cells exist to free a page inside the cap (del3: 2 of 448
cells within 2/255; las0_3's would need merging visibly different mist).

THE TRIAL: for exactly these fields the remaining FX page is converted by
the normal ff7nx_fxpages path (same art, same additive encoding) with a
ceiling of TRIAL_MB. Both land at 37.44 MB. 35.0 is not a measured failure
point: it was set as the smallest bound admitting the heaviest fields then
known (field_bg_dense, BUILD 130), and fship_2 runs at 35.3 MB. The failure
mode if the real limit is lower is black squares in that field only (the
loader stops at the first texture it cannot allocate), not a crash.

SEVENTH_NX_NO_FX_TRIAL=1 disables (both fields go back to 34.4 MB).

BUILD 618z7 -- las0_2 (hardware 10-05: "pixellation, pieces missing,
discontinuity at the 4:3 regions on certain frames" in the steam; user: "we
want to use all the hd steam effects ... 35 isnt an exact limit"). The
steam's 16:9 margin (x < -160, x >= 160, plus 88 bottom-row records) is 718
records on three paletted pages (21-23): 1x art, a 4:3 seam up to 107/255
off on the right, a grey 1x block at the top right, and 190 records whose
1x cell is blank where the Cosmos art has steam (the vertical cut at
x = 192). Cosmos's DDS for those pages has the whole effect: converted by
the normal path, the seam is <= 21/255 everywhere and every frame is
complete. Cost: 3 pages, 35.0 -> 44.19 MB of texture memory (TRIALS), and
the field decompresses to ~15.6 MB, past the 12.8 MB raw cap, so this one
field gets its own raw ceiling (RAW_CAPS) and the build's field buffer
grows 16 -> 32 MB (build.py reads `max_raw` back). The right column
(x = 192..208, where the art ends before the picture edge) fades out over
its last 12 units instead of stopping in a vertical line.
SEVENTH_NX_NO_FX_TRIAL_LAS0_2=1 drops just this field.
"""
from __future__ import annotations

import os

OFF_ENV = 'SEVENTH_NX_NO_FX_TRIAL'
TRIAL_MB = 37.5
TRIALS = {'del3': TRIAL_MB, 'las0_3': TRIAL_MB, 'las0_2': 45.0}
FIELDS = tuple(TRIALS)
# per-field raw (decompressed field) ceiling: 4/5 of a 32 MB field buffer
RAW_CAPS = {'las0_2': 32 * 1024 * 1024 * 4 // 5}
# field: (column x, fade from field x a to b)
EDGE_FADE = {'las0_2': (192, 196, 208)}
LAS0_2_ENV = 'SEVENTH_NX_NO_FX_TRIAL_LAS0_2'


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def plan_field(name, sec9, art, raw_other, raw_cap, trial_mb=TRIAL_MB,
               script=None):
    """(new section 9, info) -- raises unless exactly the expected page is
    converted and the field stays inside the trial and raw ceilings."""
    import diag_common as DC
    import field_bg_repack as FR
    import ff7nx_fxpages as FXP
    pl, _ts, _te, px = DC.parse_pages(sec9)
    left = [p.slot for p in pl if p is not None and p.depth == 1
            and FXP.FX_LO <= p.slot < FXP.FX_HI]
    if not left:
        raise ValueError('no paletted FX page left')
    run = sum(FR._page_bytes(p.px, p.depth) for p in pl if p is not None)
    animated = frozenset()
    if script is not None:
        import ff7nx_palanim
        animated = ff7nx_palanim.animated_palettes(script)
    new, st = FXP.upgrade_section9(
        name, sec9, art, px,
        max_raw_delta=max(0, raw_cap - raw_other - len(sec9)),
        max_runtime_delta=max(0, int(trial_mb * 1048576.0) - run),
        animated=animated, only_slots=set(left))
    if not st.get('pages'):
        raise ValueError('fxpages refused: %s' % {
            k: v for k, v in st.items() if k.endswith('veto') and v})
    if name.lower() in EDGE_FADE:
        new = _edge_fade(new, *EDGE_FADE[name.lower()])
    if name.lower() == 'las0_2':
        new, _pinfo = _las0_2_polish(new)
        # (618z9b: _las0_2_bottom's fill under the left margin is no longer
        # used -- on hardware it read as "a weird out of place triangle of
        # white that is always sitting there"; _las0_2_frames softens the
        # margin's lower edge instead)
        new, _finfo = _las0_2_frames(new)
    pl2, _a, _b, _c = DC.parse_pages(new)
    run2 = sum(FR._page_bytes(p.px, p.depth) for p in pl2 if p is not None)
    if run2 > trial_mb * 1048576.0 or raw_other + len(new) > raw_cap:
        raise ValueError('over the trial ceiling')
    return new, {'slots': left, 'pages': st['pages'],
                 'mb_before': round(run / 1048576.0, 2),
                 'mb': round(run2 / 1048576.0, 2)}


def _edge_fade(sec9, col, a, b):
    """Additive FX cells of the column at x = `col` (truecolor, used only at
    that x) fade linearly to black between field x `a` and `b`."""
    import collections
    import struct
    import numpy as np
    import diag_common as DC
    import field_bg_native as FN
    pl, ts, _te, px = DC.parse_pages(sec9)
    pm = {p.slot: p for p in pl if p is not None}
    k = px // 16
    xs_of = collections.defaultdict(set)
    cells = set()
    for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in offs:
            bl = sec9[o + 28]
            pg = sec9[o + 34] if bl else sec9[o + 32]
            sx, sy = struct.unpack_from('<hh', sec9, o + (14 if bl else 10))
            x = struct.unpack_from('<h', sec9, o + 2)[0]
            xs_of[(pg, sx // 16, sy // 16)].add((x, bool(bl)))
            if layer == 2 and bl and x == col and pm[pg].depth == 2:
                cells.add((pg, sx // 16, sy // 16))
    datas = {}
    t = (np.arange(k) + 0.5) * 16.0 / k + col              # field x per px
    w = np.clip((b - t) / float(b - a), 0.0, 1.0)
    for pg, cx, cy in sorted(cells):
        if xs_of[(pg, cx, cy)] != {(col, True)}:
            continue                                  # shared: leave it
        if pg not in datas:
            datas[pg] = np.frombuffer(pm[pg].data, '<u2').reshape(
                px, px).copy()
        c = datas[pg][cy * k:(cy + 1) * k, cx * k:(cx + 1) * k]
        v = c.astype(np.uint32)
        r, g, bb = (v >> 11) & 31, (v >> 6) & 31, v & 31
        r = np.floor(r * w[None, :] + 0.5).astype(np.uint32)
        g = np.floor(g * w[None, :] + 0.5).astype(np.uint32)
        bb = np.floor(bb * w[None, :] + 0.5).astype(np.uint32)
        out = (r << 11 | (g << 1) << 5 | bb).astype(np.uint16)
        out[c == 0] = 0
        c[:] = out
    if not datas:
        return sec9
    plist, t0, t1 = FN.parse_texture_block(sec9, px)
    for pg, d in datas.items():
        q = plist[pg]
        plist[pg] = FN.Page(pg, q.size_flag, 2, d.tobytes(), q.px)
    return FN.replace_texture_block(sec9, plist, t0, t1)


# ---------------------------------------------------------------------------
# BUILD 618z8 -- las0_2 steam polish (hardware 10-05, build 391)
# ---------------------------------------------------------------------------
# 1. "a weird effect symmetry ... one half is mirroring the other half": in
#    two plume frames Cosmos's 16:9 art right of x = 160 is an exact mirror
#    of the 4:3 art (mean error 2.7/255 against 20-50 in every other frame),
#    so the plume rising at the 4:3 edge opens and pinches symmetrically.
#    Those frames' margin columns are rebuilt from the same mirror but
#    horizontally compressed (MIRROR_SQUEEZE) beyond a short seam band,
#    so the join at x = 160 is unchanged and the right half is a narrower,
#    different shape instead of a reflection.
# 2. "the steam effect is lifted off the rock revealing a flat edge": see
#    _las0_2_bottom (a new row of left-margin records down to the boulder).
# 3. "this creep over the rock ... kind of dirty": the small jet low on the
#    left (y -64..-32, x -144..-64) spreads a faint grey haze over the dark
#    rock; texels under HAZE_LO are cleared and those up to HAZE_HI fade in.
MIRROR_MAX_ERR = 6.0
MIRROR_SQUEEZE = 0.6
MIRROR_SEAM = 3            # field units kept as the plain mirror at x = 160
HAZE_BOX = (-144, -64, -80, -32)     # x0, x1, y0, y1 (field units)
#                                      (618z9: from y -80, hardware 10-05
#                                      showed the haze above -64 too)
HAZE_LO, HAZE_HI = 16.0, 64.0


def _las0_2_polish(sec9):
    import collections
    import struct
    import numpy as np
    import diag_common as DC
    import field_bg_native as FN
    pl, ts, _te, px = DC.parse_pages(sec9)
    pm = {p.slot: p for p in pl if p is not None}
    k = px // 16
    U = k / 16.0                                     # HD px per field unit
    frames = collections.defaultdict(dict)           # frame -> (x,y) -> cell
    users = collections.defaultdict(set)
    for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in offs:
            bl = sec9[o + 28]
            pg = sec9[o + 34] if bl else sec9[o + 32]
            sx, sy = struct.unpack_from('<hh', sec9, o + (14 if bl else 10))
            cell = (pg, sx // 16, sy // 16)
            x, y = struct.unpack_from('<hh', sec9, o + 2)
            fr = (sec9[o + 26], sec9[o + 27]) if (layer == 2 and bl) else None
            users[cell].add((x, y, fr))
            if fr and sec9[o + 26] and pm[pg].depth == 2:
                frames[fr][(x, y)] = cell
    datas = {}

    def view(cell):
        pg, cx, cy = cell
        if pg not in datas:
            datas[pg] = np.frombuffer(pm[pg].data, '<u2').reshape(
                px, px).copy()
        return datas[pg][cy * k:(cy + 1) * k, cx * k:(cx + 1) * k]

    def rgb(v):
        v = v.astype(np.uint32)
        return np.stack([(v >> 11) & 31, (v >> 6) & 31, v & 31],
                        -1).astype(np.float32)

    def enc(c, orig):
        q = np.clip(np.floor(c + 0.5), 0, 31).astype(np.uint32)
        out = (q[..., 0] << 11 | (q[..., 1] << 1) << 5 | q[..., 2]).astype(
            np.uint16)
        out[(orig == 0) | (q.max(-1) == 0)] = 0
        return out

    def exclusive(cell, fr, xy):
        return users[cell] == {(xy[0], xy[1], fr)}

    info = {'mirror_frames': 0, 'haze_cells': 0}
    # ---- 1. the mirrored plume frames
    for fr, cells in frames.items():
        ys = sorted({y for (x, y) in cells if x in (144, 160)})
        pairs = [(y, cells.get((144, y)), cells.get((160, y))) for y in ys]
        pairs = [(y, a, b) for y, a, b in pairs if a and b]
        if not pairs:
            continue
        err, n = 0.0, 0
        for _y, a, b in pairs:
            L, R = rgb(view(a))[:, ::-1], rgb(view(b))
            m = (L.max(-1) + R.max(-1)) > 2
            err += float(np.abs(L - R)[m].sum()) * 8.0
            n += int(m.sum()) * 3
        if n == 0 or err / n > MIRROR_MAX_ERR:
            continue
        for y, a, b in pairs:
            if not exclusive(b, fr, (160, y)):
                continue
            # sources left of the edge: x = 144 and 128 (mirror distance)
            src = [rgb(view(a))[:, ::-1]]
            c128 = cells.get((128, y))
            src.append(rgb(view(c128))[:, ::-1] if c128 else
                       np.zeros_like(src[0]))
            strip = np.concatenate(src, 1)                # d = 0 .. 2k-1
            d = np.arange(k, dtype=np.float32) + 0.5
            seam = MIRROR_SEAM * U
            dd = np.where(d <= seam, d, seam + (d - seam) / MIRROR_SQUEEZE)
            i0 = np.clip(np.floor(dd - 0.5).astype(int), 0, 2 * k - 1)
            i1 = np.clip(i0 + 1, 0, 2 * k - 1)
            t = np.clip(dd - 0.5 - i0, 0, 1)[None, :, None]
            new = strip[:, i0] * (1 - t) + strip[:, i1] * t
            new[:, dd >= 2 * k - 1] = 0
            # beyond x = 176 the reflection's far side is cleared the same way
            v = view(b)
            v[:] = enc(new, np.ones_like(v))
            for xx in (176, 192):
                c2 = cells.get((xx, y))
                if c2 and exclusive(c2, fr, (xx, y)):
                    w2 = view(c2)
                    w2[:] = enc(rgb(w2) * 0.0, w2)
            info['mirror_frames'] += 1
    # ---- 3. the low jet's haze over the rock
    x0, x1, y0, y1 = HAZE_BOX
    done = set()
    for fr, cells in frames.items():
        for (x, y), cell in cells.items():
            if not (x0 <= x < x1 and y0 <= y < y1) or cell in done:
                continue
            if any(not (x0 <= ux < x1 and y0 <= uy < y1)
                   for ux, uy, _f in users[cell]):
                continue
            v = view(cell)
            c = rgb(v)
            lum = c.max(-1) * 8.0
            w = np.clip((lum - HAZE_LO) / (HAZE_HI - HAZE_LO), 0, 1)
            w = w * w * (3 - 2 * w)
            v[:] = enc(c * w[..., None], v)
            done.add(cell)
            info['haze_cells'] += 1
    if not datas:
        return sec9, info
    plist, t0, t1 = FN.parse_texture_block(sec9, px)
    for pg, dd_ in datas.items():
        q = plist[pg]
        plist[pg] = FN.Page(pg, q.size_flag, 2, dd_.tobytes(), q.px)
    return FN.replace_texture_block(sec9, plist, t0, t1), info


def _las0_2_bottom(sec9, slot=23, max_new=None):
    """las0_2 (hardware 10-05: "the steam effect is lifted off the rock
    revealing a flat edge"): the LEFT margin's steam has no records below
    y = -160 (last row y = -176), so it stops in a flat line while the
    boulder's top edge (layer 1) runs between y = -147 at x = -208 and
    -163 near x = -180. The steam is extended down to the boulder:
      * the boulder's top edge is found in layer 1 (strongest dark-above /
        light-below step between y = -164 and -136, smoothed);
      * a new row of records at y = -160 carries the steam's last rows
        smeared down to the edge, per frame and column, on free cells of
        truecolor FX page `slot` (FX additive blend is wired for slots
        15..23 only). Near-identical new cells are shared so they fit.
    Returns (section 9, info)."""
    import collections
    import struct
    import numpy as np
    from scipy import ndimage as ND
    import diag_common as DC
    import field_bg_native as FN
    import ff7nx_parallaxfill as PF
    EDGE = 2.0
    COLS = (-208, -192)
    pl, ts, _te, px = DC.parse_pages(sec9)
    pm = {p.slot: p for p in pl if p is not None}
    k = px // 16
    U = k / 16.0
    used = collections.defaultdict(set)
    binds = collections.Counter()
    users = collections.defaultdict(set)
    recs = {}                                   # (frame, x) -> record offset
    l1 = {}
    for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in offs:
            bl = sec9[o + 28]
            pg = sec9[o + 34] if bl else sec9[o + 32]
            sx, sy = struct.unpack_from('<hh', sec9, o + (14 if bl else 10))
            used[pg].add((sx // 16, sy // 16))
            binds[pg] += 1
            x, y = struct.unpack_from('<hh', sec9, o + 2)
            users[(pg, sx // 16, sy // 16)].add((x, y))
            if layer == 1 and not bl and not sec9[o + 26]:
                l1[(x, y)] = o
            if (layer == 2 and bl and sec9[o + 26] and y == -176
                    and x in COLS and pm[pg].depth == 2):
                recs[((sec9[o + 26], sec9[o + 27]), x)] = o
    if not recs or slot not in pm or pm[slot].depth != 2:
        raise ValueError('las0_2 bottom: nothing to extend')
    # ---- the boulder's top edge, per HD column, x = -208 .. -160
    Y0, Y1 = -192, -128
    W = (COLS[-1] + 16 - COLS[0]) * 3
    lum = np.zeros(((Y1 - Y0) * 3, W), np.float32)
    for (x, y), o in l1.items():
        if not (COLS[0] <= x < COLS[-1] + 16 and Y0 <= y < Y1):
            continue
        p = pm.get(sec9[o + 32])
        if p is None or p.depth != 2:
            continue
        sx, sy = struct.unpack_from('<hh', sec9, o + 10)
        v = np.frombuffer(p.data, '<u2').reshape(px, px)[
            sy // 16 * k:sy // 16 * k + k,
            sx // 16 * k:sx // 16 * k + k].astype(np.uint32)
        lum[(y - Y0) * 3:(y - Y0) * 3 + k, (x - COLS[0]) * 3:
            (x - COLS[0]) * 3 + k] = (((v >> 11) & 31) * 8
                                      + ((v >> 5) & 63) * 4
                                      + (v & 31) * 8) / 3.0
    lum = ND.gaussian_filter(lum, 1.5)
    gy = np.zeros_like(lum)
    gy[3:-3] = lum[6:] - lum[:-6]
    ys = np.arange(lum.shape[0]) / 3.0 + Y0
    band = (ys >= -164) & (ys <= -136)
    yr = ys[np.argmax(np.where(band[:, None], gy, -1e9), 0)]
    yr = ND.gaussian_filter1d(ND.median_filter(yr, 15), 8.0)
    datas = {}

    def view(pg, cx, cy):
        if pg not in datas:
            datas[pg] = np.frombuffer(pm[pg].data, '<u2').reshape(
                px, px).copy()
        return datas[pg][cy * k:(cy + 1) * k, cx * k:(cx + 1) * k]

    def rgb(v):
        v = v.astype(np.uint32)
        return np.stack([(v >> 11) & 31, (v >> 6) & 31, v & 31],
                        -1).astype(np.float32)

    def enc(c):
        q = np.clip(np.floor(c + 0.5), 0, 31).astype(np.uint32)
        out = (q[..., 0] << 11 | (q[..., 1] << 1) << 5 | q[..., 2]).astype(
            np.uint16)
        out[q.max(-1) == 0] = 0
        return out

    ty = (np.arange(k) + 0.5) / U                     # offset in the cell
    new_cells = []                                    # (frame, x, tpl, rgb)
    cut = 0
    for (fr, x), o in sorted(recs.items()):
        pg = sec9[o + 34]
        sx, sy = struct.unpack_from('<hh', sec9, o + 14)
        if users[(pg, sx // 16, sy // 16)] != {(x, -176)}:
            continue
        c0 = (x - COLS[0]) * 3
        edge = yr[c0:c0 + k]                          # field y per column
        v = view(pg, sx // 16, sy // 16)
        c = rgb(v)
        # (Cosmos's own art is already cut along the boulder where its top
        # is above -160; only the far left, where it slopes below the
        # row's end, needs more)
        # the new row y=-160: the last rows smeared down to the edge
        tail = c[-3:].mean(0)                         # (k, 3)
        wn = np.clip((edge[None, :] - (-160 + ty[:, None])) / EDGE, 0, 1)
        # 618z9b: fading from the row above down to nothing at the rock,
        # not a constant smear (hardware 10-06: "a weird out of place
        # triangle of white that is always sitting there")
        span = np.maximum(edge[None, :] - (-160.0), 1.0)
        wn = wn * np.clip(1 - ty[:, None] / span, 0, 1) ** 1.5
        cell = tail[None, :, :] * wn[..., None]
        if cell.max() >= 1:
            new_cells.append((fr, x, o, cell))
    # free cells on the FX slot; near-identical new cells share one
    fr_cells = [(cx, cy) for cy in range(16) for cx in range(16)
                if (cx, cy) not in used[slot]]
    if binds[slot] + len(new_cells) > 256:
        raise ValueError('las0_2 bottom: page %d would bind %d records'
                         % (slot, binds[slot] + len(new_cells)))
    room = len(fr_cells)
    if max_new is not None:
        room = min(room, max_new)
    if room <= 0:
        raise ValueError('las0_2 bottom: no room on page %d' % slot)
    groups = [[i] for i in range(len(new_cells))]
    reps = [new_cells[i][3] for i in range(len(new_cells))]
    while len(groups) > room:
        best = None
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                if new_cells[groups[i][0]][1] != new_cells[groups[j][0]][1]:
                    continue                          # same column only
                d = float(np.abs(reps[i] - reps[j]).mean())
                if best is None or d < best[0]:
                    best = (d, i, j)
        if best is None:
            raise ValueError('las0_2 bottom: cannot share cells')
        _d, i, j = best
        n_i, n_j = len(groups[i]), len(groups[j])
        reps[i] = (reps[i] * n_i + reps[j] * n_j) / (n_i + n_j)
        groups[i] += groups.pop(j)
        reps.pop(j)
    TILE = 52
    UV_CELL = 625000
    dd = view(slot, 0, 0) is not None and datas[slot]
    new_by_tpl = collections.defaultdict(list)
    for g, (grp, rep) in enumerate(zip(groups, reps)):
        cx, cy = fr_cells[g]
        dd[cy * k:(cy + 1) * k, cx * k:(cx + 1) * k] = enc(rep)
        for i in grp:
            _fr, x, tpl, _c = new_cells[i]
            r = bytearray(sec9[tpl:tpl + TILE])
            struct.pack_into('<hh', r, 2, x, -160)
            struct.pack_into('<hh', r, 14, cx * 16, cy * 16)
            struct.pack_into('<hh', r, 10, cx * 16, cy * 16)
            struct.pack_into('<II', r, 42, cx * UV_CELL, cy * UV_CELL)
            r[34] = slot
            new_by_tpl[tpl].append(bytes(r))
    # insert each new record right after its template (same frame, order)
    survey = DC.survey(sec9)
    rows = PF._layers(sec9, survey['back_start'], survey['tex_start'])
    lay = {r_[0]: r_ for r_ in rows}
    _l, count_at, first, count = lay[2]
    buf = bytearray(sec9)
    added = 0
    for tpl in sorted(new_by_tpl, reverse=True):
        at = tpl + TILE
        blob = b''.join(new_by_tpl[tpl])
        buf[at:at] = blob
        added += len(new_by_tpl[tpl])
    struct.pack_into('<H', buf, count_at, count + added)
    s9n = bytes(buf)
    plist, t0, t1 = FN.parse_texture_block(s9n, px)
    for pg, d in datas.items():
        q = plist[pg]
        plist[pg] = FN.Page(pg, q.size_flag, 2, d.tobytes(), q.px)
    return FN.replace_texture_block(s9n, plist, t0, t1), {
        'new_records': added, 'new_cells': len(groups), 'cut_texels': cut,
        'edge_y': (round(float(yr.min()), 1), round(float(yr.max()), 1))}


def apply_to_flevel(archive, payloads, art, encode=None, raw_cap=None,
                    log=lambda *_: None):
    import lgp
    total = {'names': [], 'refused': [], 'max_raw': 0}
    if disabled() or art is None:
        return total
    encode = encode or archive.encode_field
    for name in FIELDS:
        if name == 'las0_2' and os.environ.get(LAS0_2_ENV, '').strip(
                ).lower() in ('1', 'true', 'yes', 'on'):
            continue
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            other = sum(len(x) for x in parts) - len(parts[8])
            parts[8], info = plan_field(name, parts[8], art, other,
                                        RAW_CAPS.get(name, raw_cap or 1 << 62),
                                        trial_mb=TRIALS[name],
                                        script=parts[0])
        except Exception as exc:                               # noqa: BLE001
            total['refused'].append((name, str(exc)[:90]))
            continue
        joined = lgp.join_sections(parts)
        total['max_raw'] = max(total['max_raw'], len(joined))
        payloads[name] = encode(joined)
        total['names'].append('%s: FX page %s truecolor, %.2f -> %.2f MB'
                              % (name, ','.join(map(str, info['slots'])),
                                 info['mb_before'], info['mb']))
    return total


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  FX TRIAL (BUILD 618q): last paletted FX page made '
                   'truecolor under a per-field trial ceiling (%s). Watch '
                   'these fields for black squares. %s=1 disables.'
                   % ('; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! fx trial %s: %s' % (name, why))
    return '\n'.join(out)


# ---------------------------------------------------------------------------
# BUILD 618z9 -- las0_2 steam, frame by frame (hardware 10-05, build 392)
# ---------------------------------------------------------------------------
# "at its worst the coverage is moved and theres a weird ghost trail (or 2nd
# tail to the smoke). its like its fixed bottom randomly drifts up" / "this
# cone formation just looks like it shouldnt even be there ... the frame for
# when it starts to make this form should be removed" / "some of the smoke
# also appears to creep over the rock in weird ways".
#
# The steam is 21 frames (param 1 states 1..128, param 2 states 1..128,
# param 3 states 1..16). Measured in field space (all records of a frame
# summed, as the additive blend does):
#   * LEFT MARGIN (x < -160, Cosmos's 16:9 art): the plume's lower half is
#     a different shape in every frame (mean |frame - median| 6..21/255),
#     so its base appears to rise and fall and a second, fainter tail comes
#     and goes. Inside 4:3 the frames follow the 1997 art closely.
#     -> the margin's lower part is pulled to the per-texel MEDIAN of all
#        21 frames (weight 0 above y = MARGIN_Y0, 1 below MARGIN_Y1; 0 at the
#        4:3 seam, 1 from MARGIN_X1 outwards), so its base stays put and
#        the stray tail is gone while the top still moves.
#   * THE CONE: frames 1/1, 1/2, 1/4 (the puff at the right 4:3 edge dying
#     away) are a narrow jet centred ON the 4:3 edge; Cosmos completed the
#     1997 half-jet by mirroring it, hence a symmetric teardrop.
#     -> in those three frames the right plume (x >= CONE_X0, y < CONE_Y1,
#        soft edges) is the last big puff
#        (frame 3/16) fading out (CONE_FADE) instead: the puff dissolves.
#   * THE BOULDER: where the plume meets the boulder (x -224..-112) its
#     edge runs over the rock by up to 10 units in some frames and stops
#     short in others.
#     -> a band from ROCK_BAND[0] above to ROCK_BAND[1] below the boulder's
#        top edge (found in layer 1) is pulled to the median too (soft over
#        ROCK_SOFT units), so the steam meets the rock the same way in every
#        frame. (Cutting it at the rock instead was tried: a hard, unnatural
#        silhouette; the 1997 steam does drape over the boulder.)
# All-black additive records (167 of them, Cosmos's padding) are dropped
# first: they draw nothing and their cells hold the few new records the
# cone needs.
#
# 618z9b (hardware 10-06, build 393). THE SCRIPT SHOWS TWO FRAMES AT ONCE:
# its loop is BGON n+1 / WAIT 3 / BGOFF n-1, so the picture is always frame
# n + frame n+1 (every hardware shot matches such a pair, 12.5-13.9 against
# 13.5+ for any single frame). Hence the doubled brightness, and a "second
# tail" wherever two neighbouring Cosmos frames disagree. Also:
#   * "a lingering trail of steam at the top ... hitting the 4:3 edge and
#     creating a straight line": the 1997 jet's last wisps sit against the
#     right 4:3 edge with no margin art beside them.
#     -> SEAM_FADE: right of x = 160 - SEAM_FADE (y < CONE_Y1), where the
#        margin column next to the edge is much darker than the 4:3 side,
#        the 4:3 side fades down to the margin's level at the edge.
#   * "the effect is cutting into the rock texture and producing a sharp
#     point": the steam's own hard lower edge, doubled.
#     -> the boulder band is blurred (ROCK_BLUR units) after it is pulled
#        to the median: a soft fall-off instead of a cut.
#   * "a weird out of place triangle of white that is always sitting
#     there" (left margin, x -213..-190, y -166..-148): the fill that
#     _las0_2_bottom smeared down to the boulder (618z8), the same in
#     every frame. -> not used any more; the margin's steam continues only
#     a short way below y -160 (fading over BOTTOM_TAIL units, never past
#     the rock). A fall-off above -160 (first 618z9b try) looked indented.
#   * "very grainy, lots of banding": Cosmos's steam is noisy and 5 bits a
#     channel; two frames added doubles each step.
#     -> every frame is smoothed lightly (GRAIN_SIGMA HD px) and written
#        with an ordered dither whose pattern is INVERTED on alternate
#        frames: the two frames on screen together carry complementary
#        rounding, so their sum is close to the exact value (about one
#        extra bit) instead of two equal steps.
MARGIN_Y0, MARGIN_Y1 = -212, -188
MARGIN_X1 = -176
CONE_X0 = 96                             # the right plume: x >= this,
CONE_Y1 = -150                           # y < this (field units)
CONE_EDGE = 12                           # soft edges
CONE_FRAMES = {(1, 1): 0.40, (1, 2): 0.18, (1, 4): 0.06}
CONE_SOURCE = (3, 16)
ROCK_X1 = -112
ROCK_BAND = (-10.0, 12.0)                # units around the edge (+ = below)
ROCK_SOFT = 8.0
ROCK_BLUR = 3.0                          # field units
SEAM_FADE = 32                           # field units, right 4:3 edge
SEAM_RATIO = 0.5                         # margin < this x 4:3 side: fade
GRAIN_SIGMA = 1.6                        # HD px
BOTTOM_TAIL = 4.0                        # field units, left margin bottom
GRAIN_SIGMA_DIM = 4.0                    # HD px, where the steam is faint
GRAIN_DIM = 10.0                         # 5-bit units: "faint" below this
DITHER_MIN = 0.5                         # 5-bit units: nothing below
DITHER_LOW = 1.5                         # no dither below (no lone specks)


def _las0_2_frames(sec9, debug=None):
    import collections
    import struct
    import numpy as np
    from scipy import ndimage as ND
    import diag_common as DC
    import field_bg_native as FN
    import ff7nx_parallaxfill as PF
    TILE = 52
    UV_CELL = 625000
    pl, ts, _te, px = DC.parse_pages(sec9)
    pm = {p.slot: p for p in pl if p is not None}
    k = px // 16
    if k != 48:
        raise ValueError('las0_2 frames: pages are %d px' % px)
    K = 3
    X0, X1, Y0, Y1 = -224, 224, -256, 0
    W, H = (X1 - X0) * K, (Y1 - Y0) * K

    def dec(v):
        v = v.astype(np.uint32)
        return np.stack([(v >> 11) & 31, (v >> 6) & 31, v & 31],
                        -1).astype(np.float32)

    def enc(c):
        q = np.clip(np.floor(c + 0.5), 0, 31).astype(np.uint32)
        out = (q[..., 0] << 11 | (q[..., 1] << 1) << 5 | q[..., 2]).astype(
            np.uint16)
        out[q.max(-1) == 0] = 0
        return out

    datas = {}

    def page(pg):
        if pg not in datas:
            datas[pg] = np.frombuffer(pm[pg].data, '<u2').reshape(
                px, px).copy()
        return datas[pg]

    users = collections.Counter()
    binds = collections.Counter()
    used = collections.defaultdict(set)
    recs = collections.defaultdict(list)      # (frame, x, y) -> [offsets]
    l1 = np.zeros((H, W), np.float32)
    for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in offs:
            bl = sec9[o + 28]
            pg = sec9[o + 34] if bl else sec9[o + 32]
            sx, sy = struct.unpack_from('<hh', sec9, o + (14 if bl else 10))
            users[(pg, sx // 16, sy // 16)] += 1
            binds[pg] += 1
            used[pg].add((sx // 16, sy // 16))
            x, y = struct.unpack_from('<hh', sec9, o + 2)
            if layer == 2 and bl and sec9[o + 26]:
                if pm[pg].depth != 2:
                    raise ValueError('las0_2 frames: paletted steam page %d'
                                     % pg)
                recs[((sec9[o + 26], sec9[o + 27]), x, y)].append(o)
            elif (layer == 1 and not bl and pm[pg].depth == 2
                  and X0 <= x < X1 and Y0 <= y < Y1):
                v = np.frombuffer(pm[pg].data, '<u2').reshape(px, px)[
                    sy // 16 * k:sy // 16 * k + k,
                    sx // 16 * k:sx // 16 * k + k]
                l1[(y - Y0) * K:(y - Y0) * K + k,
                   (x - X0) * K:(x - X0) * K + k] = dec(v).mean(-1) * 8
    if any(users[(sec9[o + 34],) + tuple(
            c // 16 for c in struct.unpack_from('<hh', sec9, o + 14))] != 1
           for v in recs.values() for o in v):
        raise ValueError('las0_2 frames: shared steam cells')
    frames = sorted({f for f, _x, _y in recs})
    if len(frames) != 21 or CONE_SOURCE not in frames:
        raise ValueError('las0_2 frames: %d frames' % len(frames))

    def cell(o):
        sx, sy = struct.unpack_from('<hh', sec9, o + 14)
        return page(sec9[o + 34])[sy // 16 * k:sy // 16 * k + k,
                                  sx // 16 * k:sx // 16 * k + k]
    # ---- every frame, summed, in field space (5-bit units)
    img = {f: np.zeros((H, W, 3), np.float32) for f in frames}
    for (f, x, y), offs in recs.items():
        if not (X0 <= x < X1 and Y0 <= y < Y1):
            raise ValueError('las0_2 frames: steam at %d,%d' % (x, y))
        for o in offs:
            img[f][(y - Y0) * K:(y - Y0) * K + k,
                   (x - X0) * K:(x - X0) * K + k] += dec(cell(o))
    want = {f: a.copy() for f, a in img.items()}
    xs = np.arange(W) / K + X0 + 0.5 / K
    ys = np.arange(H) / K + Y0 + 0.5 / K
    # ---- 1. the left margin's lower part -> the median of all frames
    med = np.median(np.stack([img[f] for f in frames]), 0)
    wx = np.clip((-160 - xs) / (-160 - MARGIN_X1), 0, 1)
    wy = np.clip((ys - MARGIN_Y0) / (MARGIN_Y1 - MARGIN_Y0), 0, 1)
    wm = (wy[:, None] * wx[None, :])[..., None]
    # ---- 2. the cone frames -> the last big puff fading out
    bx = np.clip((xs - CONE_X0) / CONE_EDGE, 0, 1)
    by = np.clip((CONE_Y1 - ys) / CONE_EDGE, 0, 1)
    wc = (by[:, None] * bx[None, :])[..., None]
    for f, a in CONE_FRAMES.items():
        want[f] = want[f] * (1 - wc) + img[CONE_SOURCE] * a * wc
    # ---- 3. the boulder's top edge (layer 1): dark above, lit below
    L = ND.gaussian_filter(l1, 1.5)
    gy = np.zeros_like(L)
    gy[3:-3] = L[6:] - L[:-6]
    band = (ys >= -182) & (ys <= -140)
    edge = ys[np.argmax(np.where(band[:, None], gy, -1e9), 0)]
    edge = ND.gaussian_filter1d(ND.median_filter(edge, 21), 4.0)
    ok = (xs >= -211) & (xs <= ROCK_X1)
    edge = np.where(xs < -211, edge[ok][0], edge)
    edge = np.where(xs > ROCK_X1, edge[ok][-1], edge)
    # the band around the boulder's edge is pulled to the median as well:
    # where the steam meets the rock it does the same thing in every frame
    d = ys[:, None] - edge[None, :]            # + below the edge
    mid = (ROCK_BAND[0] + ROCK_BAND[1]) / 2.0
    half = (ROCK_BAND[1] - ROCK_BAND[0]) / 2.0
    wb = np.clip(1 - (np.abs(d - mid) - half) / ROCK_SOFT, 0, 1)
    wb = wb * np.clip((ROCK_X1 - xs) / ROCK_SOFT, 0, 1)[None, :]
    wb = np.maximum(wb[..., None], wm)
    for f in frames:
        want[f] = want[f] * (1 - wb) + med * wb
        # the band falls off softly into the rock (no hard point)
        bl = np.stack([ND.gaussian_filter(want[f][..., c], ROCK_BLUR * K)
                       for c in range(3)], -1)
        # (only ever darker: the blur rounds off hard bright points, it
        # never spreads a haze onto the rock)
        want[f] = want[f] * (1 - wb) + np.minimum(bl, want[f]) * wb
    # ---- 4. the right 4:3 edge: no straight line where the margin is empty
    e = (160 - X0) * K
    sx0 = e - SEAM_FADE * K
    rows_ = ys < CONE_Y1
    ramp = np.clip((xs[sx0:e] - xs[sx0]) / float(SEAM_FADE), 0, 1)
    for f in frames:
        a = want[f]
        ins = a[:, e - K:e].max(-1).mean(1)
        out = a[:, e:e + K].max(-1).mean(1)
        lvl = np.where(ins > 1e-3, np.clip(out / np.maximum(ins, 1e-3),
                                           0, 1), 1.0)
        on = rows_ & (lvl < SEAM_RATIO)
        if on.any():
            lvl_s = ND.gaussian_filter1d(np.where(on, lvl, 1.0), 2.0 * K)
            w = 1 - np.sqrt(ramp)[None, :] * (1 - lvl_s[:, None])
            a[:, sx0:e] *= w[..., None]
    # ---- 5. the left margin's lower edge. No records below y -160 there
    #      while the boulder is lower (x < -192): the steam continues a
    #      little way down from its last rows, fading over BOTTOM_TAIL
    #      units and stopping at the rock. (618z9b's fall-off ABOVE -160
    #      read as an indent on hardware; 618z8's fill all the way to the
    #      rock as a white triangle.)
    yb0 = (-160 - Y0) * K
    rows_b = slice(yb0, yb0 + k)
    dy = ys[rows_b] + 160.0
    reach = np.clip(1 - (ys[rows_b][:, None] - edge[None, :]) / 4.0, 0, 1)
    fall = np.exp(-dy / BOTTOM_TAIL)[:, None] * reach
    colw = np.clip((-176 - xs) / 16.0, 0, 1)[None, :]
    for f in frames:
        a = want[f]
        src = a[yb0 - 3:yb0].mean(0)
        add = src[None] * (fall * colw)[..., None]
        a[rows_b] = np.maximum(a[rows_b], add)
    # ---- 6. grain: every frame smoothed (more where the steam is faint:
    #      the thin haze is where the grain shows), kept to where the
    #      frame already has steam
    for f in frames:
        sup = (want[f].max(-1) > 0)[..., None]
        b1 = np.stack([ND.gaussian_filter(want[f][..., c], GRAIN_SIGMA)
                       for c in range(3)], -1)
        b2 = np.stack([ND.gaussian_filter(want[f][..., c], GRAIN_SIGMA_DIM)
                       for c in range(3)], -1)
        wd = np.clip(1 - b1.max(-1, keepdims=True) / GRAIN_DIM, 0, 1)
        want[f] = (b1 * (1 - wd) + b2 * wd) * sup
    # only where some frame has a record (the blurs may not open new places)
    allowed = np.zeros((H, W, 1), np.float32)
    for (_f, x, y) in recs:
        allowed[(y - Y0) * K:(y - Y0) * K + k, (x - X0) * K:(x - X0) * K + k] = 1
    allowed[rows_b, (-208 - X0) * K:(-176 - X0) * K] = 1   # step 5 row
    for f in frames:
        want[f] = want[f] * allowed
    if debug is not None:
        debug.update(img=img, want=want, edge=edge, med=med, xs=xs, ys=ys)
    # ---- write back. Every frame is re-encoded with an ordered dither in
    #      field coordinates, the pattern inverted on alternate frames (the
    #      two frames on screen together cancel each other's rounding).
    #      All-black records are dropped; a frame with two records at one
    #      place keeps one (the other is dropped) unless the sum needs both.
    bayer = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9],
                      [15, 7, 13, 5]], np.float32) / 16.0 + 1 / 32.0
    thr = np.tile(bayer, (H // 4 + 1, W // 4 + 1))[:H, :W][..., None]

    def quant(v, Y, X, odd):
        t = thr[Y:Y + k, X:X + k]
        t = 1 - t if odd else t
        q = np.floor(v + t)
        q = np.where(v < DITHER_LOW, np.floor(v + 0.5), q)
        q = np.where(v < DITHER_MIN, 0, q)
        return enc(q)
    drop = set()
    free = []
    for s in range(15, 24):
        if s in pm and pm[s].depth == 2:
            free += [(s, cx, cy) for cy in range(16) for cx in range(16)
                     if (cx, cy) not in used[s]]
    tmpl_at = {}
    for (f, x, y), offs in recs.items():
        tmpl_at.setdefault((x, y), offs[0])
    order = {f: i for i, f in enumerate(frames)}
    changed = added = 0
    new_after = collections.defaultdict(list)

    def release(o):
        drop.add(o)
        sx, sy = struct.unpack_from('<hh', sec9, o + 14)
        free.append((sec9[o + 34], sx // 16, sy // 16))
        binds[sec9[o + 34]] -= 1
    for f in frames:
        odd = order[f] % 2 == 1
        for y in range(Y0, Y1, 16):
            for x in range(X0, X1, 16):
                Y, X = (y - Y0) * K, (x - X0) * K
                D = want[f][Y:Y + k, X:X + k]
                offs = recs.get((f, x, y), [])
                if D.max() < DITHER_MIN:
                    for o in offs:
                        release(o)
                    continue
                # a sum past one record's range keeps a second record only
                # where the frame already has two there
                parts_ = ([D / 2.0, D / 2.0] if D.max() > 31.0 and
                          len(offs) >= 2 else [D])
                qs = [quant(p_, Y, X, odd ^ (i == 1))
                      for i, p_ in enumerate(parts_)]
                live = list(offs)
                for i, q in enumerate(qs):
                    if i < len(live):
                        cell(live[i])[:] = q
                        changed += 1
                        continue
                    tpl = tmpl_at.get((x, y)) or tmpl_at.get((x, y - 16))
                    if tpl is None:
                        raise ValueError('las0_2 frames: no template at '
                                         '%d,%d' % (x, y))
                    slot = next((j for j, (s_, _cx, _cy) in enumerate(free)
                                 if binds[s_] < 256), None)
                    if slot is None:
                        raise ValueError('las0_2 frames: no free FX cell')
                    s_, cx, cy = free.pop(slot)
                    binds[s_] += 1
                    page(s_)[cy * k:(cy + 1) * k, cx * k:(cx + 1) * k] = q
                    r = bytearray(sec9[tpl:tpl + TILE])
                    struct.pack_into('<hh', r, 2, x, y)
                    struct.pack_into('<hh', r, 10, cx * 16, cy * 16)
                    struct.pack_into('<hh', r, 14, cx * 16, cy * 16)
                    struct.pack_into('<II', r, 42, cx * UV_CELL,
                                     cy * UV_CELL)
                    r[34] = s_
                    r[26], r[27] = f
                    new_after[tpl].append(bytes(r))
                    added += 1
                for o in live[len(qs):]:
                    cell(o)[:] = 0
                    release(o)
    # rebuild layer 2: dropped records out, new ones after their template
    sv = DC.survey(sec9)
    rows = PF._layers(sec9, sv['back_start'], sv['tex_start'])
    lay = {r_[0]: r_ for r_ in rows}
    _l, count_at, first, count = lay[2]
    buf = bytearray(sec9)
    edits = []
    for o in drop:
        edits.append((o, TILE, b''))
    for tpl, blobs in new_after.items():
        edits.append((tpl + TILE, 0, b''.join(blobs)))
    for at, n, blob in sorted(edits, key=lambda e: (e[0], e[1]),
                              reverse=True):
        if not (first <= at <= first + count * TILE):
            raise ValueError('las0_2 frames: edit outside layer 2')
        buf[at:at + n] = blob
    struct.pack_into('<H', buf, count_at, count - len(drop) + added)
    s9n = bytes(buf)
    plist, t0, t1 = FN.parse_texture_block(s9n, px)
    for pg, d in datas.items():
        q_ = plist[pg]
        plist[pg] = FN.Page(pg, q_.size_flag, 2, d.tobytes(), q_.px)
    return FN.replace_texture_block(s9n, plist, t0, t1), {
        'dropped': len(drop), 'changed': changed, 'added': added}
