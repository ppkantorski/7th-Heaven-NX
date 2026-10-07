#!/usr/bin/env python3
"""ff7nx_fxseam.py -- close the gap between an additive light and the
silhouette it was masked to.

BUILD 617. trnad_2's sky: the sky page (layer 3) is deep blue and the
haze that makes it pale is a layer-2 ADDITIVE page masked to the rock
silhouettes. Cosmos's mask fades to zero over the last few native pixels
before the rock, while the rock itself is cut hard, so a 2-4 pixel ring
of raw deep-blue sky shows along every rock top ("the edges where it
meets the rock look weird"). The same shape exists wherever an additive
overlay is masked to an opaque cut-out (mds5_1's big screen was reported
too). FFNx shows the same ring; this project draws it better.

What changes: only texels of truecolor (depth-2) additive FX cells, only
by INCREASING them, and only in a thin band outside an opaque layer-2
cut-out, where the light just beyond the band is clearly stronger than the
light in it. The band is filled by carrying the light inward from outside
the band, ring by ring (the value just beyond, not an invented one).

Guards, all of which must hold for a texel:
  * it is drawn by exactly one static (no param/state) additive layer-2
    record on a depth-2 page, and that record is the ONLY reader of its
    cell -- so the change appears in exactly one place;
  * no static opaque layer-2 cut-out covers it (it is outside the
    silhouette), and it lies within BAND native pixels of one;
  * the carried-in light is >= MIN_LIGHT and exceeds what the texel
    already has by >= GAP_MIN in some channel.

Records, UVs, palettes, pages, slots and sizes are unchanged (checked).
SEVENTH_NX_NO_FX_SEAM=1 disables the pass.
"""
from __future__ import annotations

import os
import struct

import numpy as np

import diag_common as DC
import field_bg_native as FN

OFF_ENV = 'SEVENTH_NX_NO_FX_SEAM'
UV_SCALE = 10_000_000
UNIT = 16                 # layer-1/2 tile size in field units
BAND = 3                  # native pixels outside the silhouette
MIN_LIGHT = 16.0          # 0..255, max channel of the carried-in light
GAP_MIN = 24.0            # 0..255, the carried light must exceed it by this


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _unpack(v):
    v = v.astype(np.int32)
    r = ((v >> 11) & 31) * 255.0 / 31
    g = ((v >> 6) & 31) * 255.0 / 31
    b = (v & 31) * 255.0 / 31
    return np.stack((r, g, b), -1)


def _pack(rgb):
    q = np.clip(np.rint(rgb * 31.0 / 255.0), 0, 31).astype(np.int32)
    return ((q[..., 0] << 11) | ((q[..., 1] << 1) << 5) | q[..., 2]).astype(
        np.uint16)


# Fields where the unlit band is part of the object, not a rim. mds5_1's
# glowing grate (post-384 hardware report): the band above each bar is the
# bars' own dark thickness in Cosmos's art; lighting it thinned the bars and
# exposed their ragged cut. The rules that tell this apart generically
# (layer-1 under the band, a fade vs a cut, band colour vs object colour)
# were measured and none separates it from trnad_2's rocks and games' lamp
# posts, which the same fill fixed -- so it is named, not guessed.
SKIP_FIELDS = frozenset({'mds5_1'})


def plan_section9(sec9, name=None):
    """[(slot, py, px, new565)] and stats. Only what the guards prove."""
    pages_l, tex_start, _te, page_px = DC.parse_pages(sec9)
    pages = {p.slot: p for p in pages_l if p is not None}
    back = sec9.find(b'BACK')
    rows = [(layer, off) for layer, offs in DC.walk_layers(sec9, back,
                                                           tex_start)
            for off in offs]
    st = {'texels': 0, 'cells': 0}
    if not rows or (name or '').lower() in SKIP_FIELDS:
        return [], st

    def cell_of(off, slot):
        page = pages.get(slot)
        if page is None or page.size_flag:
            return None
        grid = 16
        step = page.px // grid
        u, v = struct.unpack_from('<II', sec9, off + 42)
        return (slot, int(round(u / UV_SCALE * grid)) * step,
                int(round(v / UV_SCALE * grid)) * step, step)

    readers = {}
    fx, occ = [], []
    for layer, off in rows:
        use_fx = sec9[off + 28]
        for sl in ((sec9[off + 32], sec9[off + 34]) if use_fx
                   else (sec9[off + 32],)):
            c = cell_of(off, sl)
            if c is not None:
                readers[c[:3]] = readers.get(c[:3], 0) + 1
        if layer != 2 or sec9[off + 26] or sec9[off + 27]:
            continue
        if use_fx:
            if sec9[off + 30] != 1:
                continue
            c = cell_of(off, sec9[off + 34])
            if c and pages[c[0]].depth == 2 and pages[c[0]].px == page_px:
                fx.append((off, c))
        else:
            c = cell_of(off, sec9[off + 32])
            if c and pages[c[0]].depth == 2 and pages[c[0]].px == page_px:
                occ.append((off, c))
    if not fx or not occ:
        return [], st
    scale = page_px // 256
    step = UNIT * scale
    xs = [struct.unpack_from('<h', sec9, o + 2)[0] for o, _c in fx + occ]
    ys = [struct.unpack_from('<h', sec9, o + 4)[0] for o, _c in fx + occ]
    x0, y0 = min(xs), min(ys)
    W = (max(xs) - x0 + UNIT) * scale
    H = (max(ys) - y0 + UNIT) * scale

    solid = np.zeros((H, W), bool)
    for off, (slot, cx, cy, s_) in occ:
        if s_ != step:
            continue
        dx = (struct.unpack_from('<h', sec9, off + 2)[0] - x0) * scale
        dy = (struct.unpack_from('<h', sec9, off + 4)[0] - y0) * scale
        blk = np.frombuffer(pages[slot].data, '<u2').reshape(
            page_px, page_px)[cy:cy + step, cx:cx + step]
        solid[dy:dy + step, dx:dx + step] |= blk != FN.EMPTY

    light = np.zeros((H, W, 3), np.float32)
    owner = np.full((H, W), -1, np.int32)
    count = np.zeros((H, W), np.int16)
    for i, (off, (slot, cx, cy, s_)) in enumerate(fx):
        if s_ != step:
            continue
        dx = (struct.unpack_from('<h', sec9, off + 2)[0] - x0) * scale
        dy = (struct.unpack_from('<h', sec9, off + 4)[0] - y0) * scale
        blk = np.frombuffer(pages[slot].data, '<u2').reshape(
            page_px, page_px)[cy:cy + step, cx:cx + step]
        light[dy:dy + step, dx:dx + step] += _unpack(blk) * (
            blk != FN.EMPTY)[..., None]
        owner[dy:dy + step, dx:dx + step] = i
        count[dy:dy + step, dx:dx + step] += 1
    usable = (count == 1)
    for i, (off, c) in enumerate(fx):
        if readers.get(c[:3], 0) != 1:
            usable &= owner != i

    # Ring distance from the silhouette, in texels, out to the band plus a
    # margin to carry the light from.
    band = BAND * scale
    dist = np.zeros((H, W), np.int16)
    grown = solid.copy()
    for d in range(1, band + 3):
        nxt = grown.copy()
        nxt[1:] |= grown[:-1]
        nxt[:-1] |= grown[1:]
        nxt[:, 1:] |= grown[:, :-1]
        nxt[:, :-1] |= grown[:, 1:]
        dist[nxt & ~grown] = d
        grown = nxt
    have = count > 0
    val = light.copy()
    lum = light.max(-1)
    for d in range(band, 0, -1):
        ring = (dist == d) & have
        if not ring.any():
            continue
        acc = np.zeros_like(val)
        n = np.zeros((H, W), np.float32)
        for sy, sx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            src_d = np.roll(np.roll(dist, sy, 0), sx, 1)
            src_v = np.roll(np.roll(val, sy, 0), sx, 1)
            src_h = np.roll(np.roll(have, sy, 0), sx, 1)
            m = (src_d > d) & src_h
            acc += src_v * m[..., None]
            n += m
        ok = ring & (n > 0)
        carried = np.zeros_like(val)
        carried[ok] = acc[ok] / n[ok][:, None]
        val[ok] = np.maximum(val[ok], carried[ok])

    newlum = val.max(-1)
    gain = (val - light).max(-1)
    change = ((dist >= 1) & (dist <= band) & have & usable & ~solid
              & (newlum >= MIN_LIGHT) & (gain >= GAP_MIN))
    if os.environ.get('FXSEAM_DIAG'):
        inb = (dist >= 1) & (dist <= band) & ~solid
        print('band', int(inb.sum()), 'noFX', int((inb & ~have).sum()),
              'multi', int((inb & (count > 1)).sum()),
              'shared', int((inb & have & (count == 1) & ~usable).sum()),
              'dim', int((inb & have & usable & (newlum < MIN_LIGHT)).sum()),
              'small', int((inb & have & usable & (newlum >= MIN_LIGHT)
                            & (gain < GAP_MIN)).sum()),
              'change', int(change.sum()))
        for d in range(1, band + 1):
            r = (dist == d) & ~solid & have
            print('  d', d, 'n', int(r.sum()), 'lum %.1f -> %.1f' % (
                lum[r].mean() if r.any() else 0,
                newlum[r].mean() if r.any() else 0))
    if not change.any():
        return [], st
    plans = []
    cells = set()
    for y, x in zip(*np.nonzero(change)):
        i = owner[y, x]
        off, (slot, cx, cy, _s) = fx[i]
        dx = (struct.unpack_from('<h', sec9, off + 2)[0] - x0) * scale
        dy = (struct.unpack_from('<h', sec9, off + 4)[0] - y0) * scale
        v = int(_pack(val[y, x][None])[0])
        if v == FN.EMPTY:
            continue
        plans.append((slot, cy + (y - dy), cx + (x - dx), v))
        cells.add((slot, cx, cy))
    st['texels'] = len(plans)
    st['cells'] = len(cells)
    return plans, st


ANIM_OFF_ENV = 'SEVENTH_NX_NO_ANIM_CLEAR'
EDGE_TRIM = 3            # native pixels from the mod's own outline
KILL_MAX = 0.35          # of a cell's drawn texels, at most


def anim_disabled():
    return os.environ.get(ANIM_OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def plan_anim_clear(name, sec9, cosmos9, provider):
    """[(slot, py, px, 0)] -- key the parts of an ANIMATED opaque overlay
    that the mod paints nowhere, where a static layer shows through.

    mds5_1's big screen (post-384 hardware report: a dirty dark line around
    the picture): the screen's animation states (param/state records
    on layer 2) keep their 1997 pixels at 3x, because an animated cell is
    never replaced wholesale. The 1997 screen rectangle is larger than the
    opening Cosmos cut into its HD bezel, so it laps over the bezel -- the
    layers do not meet like puzzle pieces. Where EVERY Cosmos image of that
    page (every palette, every state the art sources hold) is transparent,
    and a static opaque tile drawn BEHIND the overlay covers the texel, the
    texel is keyed: the bezel shows, as in the mod. Nothing is painted.
    """
    import ff7nx_lostdetail as LD
    st = {'texels': 0, 'cells': 0}
    if anim_disabled() or not cosmos9:
        return [], st
    pages_l, tex_start, _te, page_px = DC.parse_pages(sec9)
    pages = {p.slot: p for p in pages_l if p is not None}
    lb, lc = LD._layers(sec9), LD._layers(cosmos9)
    field = name.lower()
    scale = page_px // 256
    step = UNIT * scale

    def cell_of(off, slot):
        page = pages.get(slot)
        if page is None or page.size_flag:
            return None
        u, v = struct.unpack_from('<II', sec9, off + 42)
        return (slot, int(round(u / UV_SCALE * 16)) * step,
                int(round(v / UV_SCALE * 16)) * step)

    readers = {}
    statics = []
    for layer, offs in lb.items():
        for off in offs:
            use_fx = sec9[off + 28]
            xy = sec9[off + 2:off + 6]
            for sl in ((sec9[off + 32], sec9[off + 34]) if use_fx
                       else (sec9[off + 32],)):
                c = cell_of(off, sl)
                if c is not None:
                    readers.setdefault(c, set()).add((xy, bool(use_fx)))
            if (layer in (1, 2) and not use_fx and not sec9[off + 26]
                    and not sec9[off + 27]):
                c = cell_of(off, sec9[off + 32])
                if c and pages[c[0]].depth == 2 and pages[c[0]].px == page_px:
                    statics.append((layer, off, c))
    # static cover per destination tile position
    cover = {}
    for layer, off, (slot, cx, cy) in statics:
        x, y = struct.unpack_from('<hh', sec9, off + 2)
        z = struct.unpack_from('<I', sec9, off + 38)[0]
        blk = np.frombuffer(pages[slot].data, '<u2').reshape(
            page_px, page_px)[cy:cy + step, cx:cx + step] != FN.EMPTY
        cover.setdefault((x, y), []).append((layer, z, blk))

    alpha_cache = {}

    def clear_everywhere(cslot):
        """Texels (768 page space) transparent in every Cosmos image."""
        if cslot in alpha_cache:
            return alpha_cache[cslot]
        keys = [k for k in getattr(provider, 'state_slots', {})
                if k[0] == field and k[1] == cslot]
        out = None
        import field_bg_repack as FR
        for k in keys:
            for path, entry in provider.state_slots[k]:
                reader = provider.readers.get(path)
                if reader is None:
                    reader = provider.readers[path] = FR.IroReader(path)
                blob = reader.read(entry)
                if not blob:
                    alpha_cache[cslot] = None
                    return None
                art = FR.PageArt(blob, page_px)
                tm = np.asarray(art.tmask).reshape(page_px, page_px)
                out = tm.copy() if out is None else (out & tm)
        alpha_cache[cslot] = out
        return out

    plans = []
    cells = set()
    for layer in (1, 2):
        ob, oc = lb.get(layer, []), lc.get(layer, [])
        if len(ob) < len(oc):
            return [], st
        for o, q in zip(ob, oc):
            if LD._key(sec9, o) != LD._key(cosmos9, q):
                return [], st
            if (layer != 2 or sec9[o + 28]
                    or not sec9[o + 26]):
                continue
            c = cell_of(o, sec9[o + 32])
            if c is None or c in cells:
                continue
            slot, cx, cy = c
            page = pages[slot]
            if page.depth != 2 or page.px != page_px:
                continue
            xy = sec9[o + 2:o + 6]
            if readers.get(c) != {(xy, False)}:
                continue                    # shared elsewhere / by FX
            clr = clear_everywhere(cosmos9[q + 32])
            if clr is None:
                continue
            sx = struct.unpack_from('<H', cosmos9, q + 10)[0] * scale
            sy = struct.unpack_from('<H', cosmos9, q + 12)[0] * scale
            cclr = clr[sy:sy + step, sx:sx + step]
            if cclr.shape != (step, step):
                continue
            x, y = struct.unpack_from('<hh', sec9, o + 2)
            z = struct.unpack_from('<I', sec9, o + 38)[0]
            behind = np.zeros((step, step), bool)
            for l2, z2, blk in cover.get((x, y), ()):
                if l2 == 1 or z2 > z:
                    behind |= blk
            blk = np.frombuffer(page.data, '<u2').reshape(
                page_px, page_px)[cy:cy + step, cx:cx + step]
            drawn = blk != FN.EMPTY
            kill = drawn & cclr & behind
            # EDGE TRIM ONLY. The mod's art must be present in this cell,
            # and a texel is trimmed only within EDGE_TRIM native pixels of
            # it, so a state the mod did not paint (all transparent) or a
            # piece of 1997 art away from the mod's outline is never
            # removed -- only the 1997 outline lapping past the mod's.
            opaque = ~cclr
            if opaque.mean() < 0.05:
                continue
            near = opaque.copy()
            for _ in range(EDGE_TRIM * scale):
                g = near.copy()
                g[1:] |= near[:-1]
                g[:-1] |= near[1:]
                g[:, 1:] |= near[:, :-1]
                g[:, :-1] |= near[:, 1:]
                near = g
            kill &= near
            if kill.sum() > KILL_MAX * max(1, drawn.sum()):
                continue
            if os.environ.get('FXSEAM_DIAG'):
                print('  anim', (x, y), sec9[o + 26], sec9[o + 27], 'drawn',
                      int((blk != FN.EMPTY).sum()), 'clear',
                      int(((blk != FN.EMPTY) & cclr).sum()), 'behind',
                      int(behind.sum()), 'covers', len(cover.get((x, y), ())),
                      'kill', int(kill.sum()))
            if not kill.any():
                continue
            cells.add(c)
            for py, px in zip(*np.nonzero(kill)):
                plans.append((slot, cy + py, cx + px, FN.EMPTY))
    st['texels'] = len(plans)
    st['cells'] = len(cells)
    return plans, st


# ---------------------------------------------------------------- BUILD 617b
# ANIMATED OVERLAY EDGE CLOSE. mds5_1's big screen, after the trim above,
# still meets its bezel with a ragged edge: the 1997 picture is 3x-blocky
# and its outline has small bays where the static layer behind shows
# through -- a sliver of the bezel's white highlight above its lower edge
# and a dark notch on its left edge (latest hardware report).
# Close those bays: per animation state, the overlay's opaque mask is
# morphologically CLOSED (dilate then erode by CLOSE_R native pixels), and
# only texels the closing adds, that a static tile behind covers and that
# lie inside one of the state's own exclusive cells, are painted -- with the
# overlay's own neighbouring colour carried outward. Convex corners and the
# outline's overall shape are untouched by construction; only bays narrower
# than 2*CLOSE_R close.
CLOSE_OFF_ENV = 'SEVENTH_NX_NO_ANIM_CLOSE'
CLOSE_FIELDS = {'mds5_1': frozenset({1})}      # field -> animation params (the screen)
CLOSE_R = 1               # native pixels (x3 at 768)
CLOSE_MAX = 0.15          # of a state's drawn texels, at most


SMOOTH_SIGMA = 0.7        # native pixels
GROW = 1                  # native pixels: covers the static sliver


def _blur(a, sigma):
    r = max(1, int(round(sigma * 2.5)))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / float(sigma)) ** 2)
    k /= k.sum()
    p = np.pad(a, r, mode='edge')
    p = np.apply_along_axis(lambda v: np.convolve(v, k, 'valid'), 0, p)
    return np.apply_along_axis(lambda v: np.convolve(v, k, 'valid'), 1, p)


def close_disabled():
    return os.environ.get(CLOSE_OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _grow_colour(rgb, src, want):
    """RGB for `want` texels: the mean of already-set 4-neighbours, ring by
    ring outward from `src`."""
    have = src.copy()
    val = rgb * have[..., None]
    todo = want & ~have
    H, W = have.shape
    for _ in range(256):
        if not todo.any():
            break
        acc = np.zeros_like(val)
        n = np.zeros(have.shape, np.float32)
        for sy, sx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            h = np.zeros_like(have)
            v = np.zeros_like(val)
            h[max(-sy, 0):H + min(-sy, 0), max(-sx, 0):W + min(-sx, 0)] = \
                have[max(sy, 0):H + min(sy, 0), max(sx, 0):W + min(sx, 0)]
            v[max(-sy, 0):H + min(-sy, 0), max(-sx, 0):W + min(-sx, 0)] = \
                val[max(sy, 0):H + min(sy, 0), max(sx, 0):W + min(sx, 0)]
            acc += v * h[..., None]
            n += h
        ring = todo & (n > 0)
        val[ring] = acc[ring] / n[ring][:, None]
        have |= ring
        todo &= ~ring
    return val


def _morph(m, r, grow):
    out = m.copy()
    for _ in range(r):
        g = out.copy()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy == 0 and dx == 0:
                    continue
                sh = np.zeros_like(out)
                H, W = out.shape
                sh[max(dy, 0):H + min(dy, 0), max(dx, 0):W + min(dx, 0)] = \
                    out[max(-dy, 0):H + min(-dy, 0), max(-dx, 0):W + min(-dx, 0)]
                g = (g | sh) if grow else (g & sh)
        out = g
    return out


def plan_anim_close(name, sec9):
    """[(slot, py, px, value)] and stats."""
    st = {'texels': 0, 'cells': 0}
    if close_disabled() or name.lower() not in CLOSE_FIELDS:
        return [], st
    pages_l, tex_start, _te, page_px = DC.parse_pages(sec9)
    pages = {p.slot: p for p in pages_l if p is not None}
    scale = page_px // 256
    step = UNIT * scale
    R = CLOSE_R * scale

    def cell_of(off, slot):
        page = pages.get(slot)
        if page is None or page.size_flag:
            return None
        u, v = struct.unpack_from('<II', sec9, off + 42)
        return (slot, int(round(u / UV_SCALE * 16)) * step,
                int(round(v / UV_SCALE * 16)) * step)

    rows = [(layer, off) for layer, offs in DC.walk_layers(
        sec9, sec9.find(b'BACK'), tex_start) for off in offs]
    readers = {}
    groups, statics = {}, []
    for layer, off in rows:
        use_fx = sec9[off + 28]
        for sl in ((sec9[off + 32], sec9[off + 34]) if use_fx
                   else (sec9[off + 32],)):
            c = cell_of(off, sl)
            if c is not None:
                readers[c] = readers.get(c, 0) + 1
        if layer not in (1, 2) or use_fx:
            continue
        c = cell_of(off, sec9[off + 32])
        if c is None or pages[c[0]].depth != 2 or pages[c[0]].px != page_px:
            continue
        if sec9[off + 26]:
            if layer == 2 and sec9[off + 26] in CLOSE_FIELDS[name.lower()]:
                groups.setdefault((sec9[off + 26], sec9[off + 27]),
                                  []).append((off, c))
        elif not sec9[off + 27]:
            statics.append((layer, off, c))

    def blk(c):
        slot, cx, cy = c
        return np.frombuffer(pages[slot].data, '<u2').reshape(
            page_px, page_px)[cy:cy + step, cx:cx + step]

    plans, cells = [], set()
    for key, recs in sorted(groups.items()):
        xs = [struct.unpack_from('<h', sec9, o + 2)[0] for o, _c in recs]
        ys = [struct.unpack_from('<h', sec9, o + 4)[0] for o, _c in recs]
        zmax = max(struct.unpack_from('<I', sec9, o + 38)[0] for o, _c in recs)
        x0, y0 = min(xs), min(ys)
        W = (max(xs) - x0 + UNIT) * scale + 2 * R
        H = (max(ys) - y0 + UNIT) * scale + 2 * R
        drawn = np.zeros((H, W), bool)
        rgb = np.zeros((H, W, 3), np.float32)
        owner = np.full((H, W), -1, np.int32)
        count = np.zeros((H, W), np.int16)
        for i, (o, c) in enumerate(recs):
            x, y = struct.unpack_from('<hh', sec9, o + 2)
            dx, dy = (x - x0) * scale + R, (y - y0) * scale + R
            b = blk(c)
            if b.shape != (step, step):
                continue
            sl = (slice(dy, dy + step), slice(dx, dx + step))
            drawn[sl] |= b != FN.EMPTY
            rgb[sl] = np.where((b != FN.EMPTY)[..., None], _unpack(b), rgb[sl])
            owner[sl] = i
            count[sl] += 1
        cover = np.zeros((H, W), bool)
        for layer, o, c in statics:
            x, y = struct.unpack_from('<hh', sec9, o + 2)
            z = struct.unpack_from('<I', sec9, o + 38)[0]
            if layer == 2 and z <= zmax:
                continue
            dx, dy = (x - x0) * scale + R, (y - y0) * scale + R
            if dx + step <= 0 or dy + step <= 0 or dx >= W or dy >= H:
                continue
            b = blk(c) != FN.EMPTY
            ys_, xs_ = max(dy, 0), max(dx, 0)
            ye, xe = min(dy + step, H), min(dx + step, W)
            cover[ys_:ye, xs_:xe] |= b[ys_ - dy:ye - dy, xs_ - dx:xe - dx]
        # Closing fills bays; a light Gaussian on the mask then rounds the
        # remaining 3x steps and their corner tips. Both only within R of
        # the original outline.
        closed = _morph(_morph(drawn, R, True), R, False)
        if GROW:
            closed = _morph(closed, GROW * scale, True)
        smooth = _blur(closed.astype(np.float32), SMOOTH_SIGMA * scale) > 0.5
        near = _morph(drawn, R, True) & ~_morph(drawn, R, False)
        new = np.where(near, smooth, drawn)
        ok_owner = np.zeros(len(recs), bool)
        for i, (o, c) in enumerate(recs):
            ok_owner[i] = readers.get(c, 0) == 1
        mine = (owner >= 0) & ok_owner[np.maximum(owner, 0)] & (count == 1) \
            & cover
        fill = new & ~drawn & mine
        kill = drawn & ~new & mine
        if not (fill.any() or kill.any()) or \
                fill.sum() + kill.sum() > CLOSE_MAX * max(1, drawn.sum()):
            continue
        # Colour from the picture's interior, not its outermost 3x ring
        # (the 1997 outline is darkened and would streak outward).
        # The old outline ring next to what was grown is repainted the same
        # way, or it would stay behind as a dark line inside the picture.
        src = _morph(drawn & ~kill, scale, False)
        ring = drawn & ~kill & ~src & _morph(fill, scale, True) & mine
        fill = fill | ring
        val = _pack(_grow_colour(rgb, src, fill))
        val[val == FN.EMPTY] = FN.NEAR_BLACK
        for m, fixed in ((fill, None), (kill, FN.EMPTY)):
            for yy, xx in zip(*np.nonzero(m)):
                o, (slot, cx, cy) = recs[owner[yy, xx]]
                x, y = struct.unpack_from('<hh', sec9, o + 2)
                dx, dy = (x - x0) * scale + R, (y - y0) * scale + R
                plans.append((slot, cy + yy - dy, cx + xx - dx,
                              int(val[yy, xx]) if fixed is None else fixed))
                cells.add((slot, cx, cy))
    st['texels'] = len(plans)
    st['cells'] = len(cells)
    return plans, st


# ---------------------------------------------------------------- BUILD 617c
# RIM DE-SPIKE. mds5_1's grate: the static additive glow is cut around the
# four bars, and the texels on the cut are 2-3x brighter than the glow a
# few texels away (a bright one-to-two texel rim, 186-210 against ~90-150)
# -- a glowing seam along the top and bottom edge of every bar (hardware
# report). Rim texels within RIM_D texels of a keyed (zero) texel whose
# brightness exceeds the glow just behind them are brought down to that
# glow, tapering toward the cut. Only decreases; only exclusive static
# additive d2 cells inside the target rectangle.
RIM_OFF_ENV = 'SEVENTH_NX_NO_RIM_DESPIKE'
RIM_TARGETS = {'mds5_1': (-160, -16, -64, 80)}     # x0, y0, x1, y1 units
RIM_D = 6                 # texels at 768 re-shaped next to the cut (2 native px)
RIM_REF = (7, 10)         # texels: the glow just behind the rim
RIM_TAPER = (0.55, 0.7, 0.82, 0.9, 0.96, 1.0)  # of the reference, d=1..6
RIM_SMOOTH = 0           # texels (768): Gaussian over the lit glow (off)
GLOW_CLOSE = 5           # texels (768): dark streaks narrower than this fill


def rim_disabled():
    return os.environ.get(RIM_OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def plan_rim_despike(name, sec9):
    st = {'texels': 0, 'cells': 0}
    box = RIM_TARGETS.get(name.lower())
    if box is None or rim_disabled():
        return [], st
    pages_l, tex_start, _te, page_px = DC.parse_pages(sec9)
    pages = {p.slot: p for p in pages_l if p is not None}
    scale = page_px // 256
    step = UNIT * scale
    rows = [(layer, off) for layer, offs in DC.walk_layers(
        sec9, sec9.find(b'BACK'), tex_start) for off in offs]

    def cell_of(off, slot):
        page = pages.get(slot)
        if page is None or page.size_flag:
            return None
        u, v = struct.unpack_from('<II', sec9, off + 42)
        return (slot, int(round(u / UV_SCALE * 16)) * step,
                int(round(v / UV_SCALE * 16)) * step)

    readers, fx = {}, []
    for layer, off in rows:
        use_fx = sec9[off + 28]
        for sl in ((sec9[off + 32], sec9[off + 34]) if use_fx
                   else (sec9[off + 32],)):
            c = cell_of(off, sl)
            if c is not None:
                readers[c] = readers.get(c, 0) + 1
        if (layer == 2 and use_fx and sec9[off + 30] == 1
                and not sec9[off + 26] and not sec9[off + 27]):
            x, y = struct.unpack_from('<hh', sec9, off + 2)
            c = cell_of(off, sec9[off + 34])
            if (c and box[0] <= x and x + UNIT <= box[2] and box[1] <= y
                    and y + UNIT <= box[3] and pages[c[0]].depth == 2
                    and pages[c[0]].px == page_px):
                fx.append((off, c))
    if not fx:
        return [], st
    x0, y0 = box[0], box[1]
    W, H = (box[2] - x0) * scale, (box[3] - y0) * scale
    rgb = np.zeros((H, W, 3), np.float32)
    lit = np.zeros((H, W), bool)
    keyed = np.zeros((H, W), bool)
    owner = np.full((H, W), -1, np.int32)
    count = np.zeros((H, W), np.int16)
    for i, (o, (slot, cx, cy)) in enumerate(fx):
        x, y = struct.unpack_from('<hh', sec9, o + 2)
        dx, dy = (x - x0) * scale, (y - y0) * scale
        b = np.frombuffer(pages[slot].data, '<u2').reshape(
            page_px, page_px)[cy:cy + step, cx:cx + step]
        sl = (slice(dy, dy + step), slice(dx, dx + step))
        v = _unpack(b)
        rgb[sl] = v
        keyed[sl] = (b == FN.EMPTY) | (v.max(-1) < 4)
        lit[sl] = ~keyed[sl]
        owner[sl] = i
        count[sl] += 1
    orig = rgb.copy()
    # Smooth the glow itself (normalised over lit texels only, so no light
    # leaks into the cut and the cut edge stays where it is): the Cosmos
    # glow is a soft gradient, and its 3x stair-steps read as aliasing.
    if RIM_SMOOTH:
        w = lit.astype(np.float32)
        den = _blur(w, RIM_SMOOTH)
        for ch in range(3):
            num = _blur(rgb[..., ch] * w, RIM_SMOOTH)
            rgb[..., ch] = np.where(lit & (den > 1e-3),
                                    num / np.maximum(den, 1e-3), rgb[..., ch])
    # distance (in texels, 4-neighbour rings) from the cut
    dist = np.zeros((H, W), np.int16)
    grown = keyed.copy()
    for d in range(1, RIM_REF[1] + 1):
        g = _morph4(grown)
        dist[g & ~grown] = d
        grown = g
    lum = rgb.max(-1)
    ref_m = lit & (dist >= RIM_REF[0]) & (dist <= RIM_REF[1])
    k = 2 * RIM_REF[1] + 1
    acc = _boxsum(rgb * ref_m[..., None], k)
    n = _boxsum(ref_m.astype(np.float32), k)
    ref = np.where(n[..., None] > 0, acc / np.maximum(n, 1)[..., None], 0)
    for d, taper in zip(range(1, RIM_D + 1), RIM_TAPER):
        # Only lowered, never raised: raising the dips spilled haze and
        # specks along the ragged cut.
        m = (dist == d) & lit & (n > 0)
        target = ref * taper
        m &= lum > target.max(-1) * 1.05
        rgb[m] = np.minimum(rgb[m], target[m])
    # DARK FRINGES. The Cosmos glow carries thin dark streaks (one or two
    # texels) running along the bars inside the light, which read as dirty
    # aliasing. A grey-scale closing (GLOW_CLOSE x GLOW_CLOSE) fills valleys
    # narrower than the window and leaves the bar cuts (much wider) and the
    # peaks alone; keyed texels stay keyed and nothing is lowered.
    if GLOW_CLOSE:
        filled = rgb * lit[..., None]
        closed = _grey_close(filled, GLOW_CLOSE)
        rgb = np.where(lit[..., None], np.maximum(rgb, closed), rgb)
    ok = np.array([readers.get(c) == 1 for _o, c in fx])
    mine = lit & (count == 1) & (owner >= 0) & ok[np.maximum(owner, 0)]
    newv = _pack(rgb)
    newv[newv == FN.EMPTY] = FN.NEAR_BLACK
    change = mine & (newv != _pack(orig))
    plans, cells = [], set()
    for yy, xx in zip(*np.nonzero(change)):
        o, (slot, cx, cy) = fx[owner[yy, xx]]
        x, y = struct.unpack_from('<hh', sec9, o + 2)
        dx, dy = (x - x0) * scale, (y - y0) * scale
        plans.append((slot, cy + yy - dy, cx + xx - dx, int(newv[yy, xx])))
        cells.add((slot, cx, cy))
    st['texels'] = len(plans)
    st['cells'] = len(cells)
    return plans, st


def _grey_close(a, k):
    """Grey-scale closing of an (H, W, C) image with a k x k square."""
    r = k // 2

    def shift_all(img, op):
        out = img.copy()
        H, W = img.shape[:2]
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if dy == 0 and dx == 0:
                    continue
                sh = np.full_like(img, np.nan)
                sh[max(dy, 0):H + min(dy, 0), max(dx, 0):W + min(dx, 0)] = \
                    img[max(-dy, 0):H + min(-dy, 0),
                        max(-dx, 0):W + min(-dx, 0)]
                out = op(out, np.where(np.isnan(sh), out, sh))
        return out
    return shift_all(shift_all(a, np.maximum), np.minimum)


def _morph4(m):
    g = m.copy()
    g[1:] |= m[:-1]
    g[:-1] |= m[1:]
    g[:, 1:] |= m[:, :-1]
    g[:, :-1] |= m[:, 1:]
    return g


def _boxsum(a, k):
    r = k // 2
    p = np.pad(a, [(r, r), (r, r)] + [(0, 0)] * (a.ndim - 2))
    c = p.cumsum(0).cumsum(1)
    c = np.pad(c, [(1, 0), (1, 0)] + [(0, 0)] * (a.ndim - 2))
    H, W = a.shape[:2]
    return (c[k:k + H, k:k + W] - c[:H, k:k + W] - c[k:k + H, :W]
            + c[:H, :W])


# ---------------------------------------------------------------- BUILD 617e
# GRATE TOP FACES. mds5_1's four grate bars are drawn as dark slots cut
# into a static additive glow. With Cosmos's art the slots read as thin dark
# lines floating in the light: the bars have no top surface (hardware
# report: "the flat brown part is missing"). Give each bar a flat wooden top
# face directly above it: a strip GRATE_T texels thick that follows the
# bar's own top edge column by column, bevelled at 45 degrees at the free
# (left) end, painted wood-brown with a lighter front edge, a darker back
# edge and the underlying texture's own light/dark variation, onto the
# topmost static tile at each texel; the glow over the face is keyed so the
# face is not lit through. Runs before the rim pass, which then fades the
# glow softly into the new top edge. mds5_1 box only.
GRATE_OFF_ENV = 'SEVENTH_NX_NO_GRATE_FACES'
GRATE_TARGETS = {'mds5_1': (-160, -16, -64, 80)}
GRATE_T = 7               # texels (768): face depth, about 2.3 units
GRATE_WOOD = (104.0, 86.0, 64.0)
GRATE_DARK = 50           # max channel: the bar slot in the static art


GRATE_ON_ENV = 'SEVENTH_NX_GRATE_FACES'   # opt-in: superseded by a painted
#                                           texture_edits/ patch (ff7nx_texpatch)


def grate_disabled():
    if os.environ.get(GRATE_OFF_ENV, '').strip().lower() in (
            '1', 'true', 'yes', 'on'):
        return True
    return os.environ.get(GRATE_ON_ENV, '').strip().lower() not in (
        '1', 'true', 'yes', 'on')


def plan_grate_faces(name, sec9):
    st = {'texels': 0, 'bars': 0}
    box = GRATE_TARGETS.get(name.lower())
    if box is None or grate_disabled():
        return [], st
    pages_l, tex_start, _te, page_px = DC.parse_pages(sec9)
    pages = {p.slot: p for p in pages_l if p is not None}
    scale = page_px // 256
    step = UNIT * scale

    def cell_of(off, slot):
        page = pages.get(slot)
        if page is None or page.size_flag or page.depth != 2 \
                or page.px != page_px:
            return None
        u, v = struct.unpack_from('<II', sec9, off + 42)
        return (slot, int(round(u / UV_SCALE * 16)) * step,
                int(round(v / UV_SCALE * 16)) * step)

    rows = [(layer, off) for layer, offs in DC.walk_layers(
        sec9, sec9.find(b'BACK'), tex_start) for off in offs]
    readers = {}
    fx, stat = [], []
    for layer, off in rows:
        use_fx = sec9[off + 28]
        for sl in ((sec9[off + 32], sec9[off + 34]) if use_fx
                   else (sec9[off + 32],)):
            c = cell_of(off, sl)
            if c is not None:
                readers[c] = readers.get(c, 0) + 1
        if sec9[off + 26] or sec9[off + 27] or layer not in (1, 2):
            continue
        x, y = struct.unpack_from('<hh', sec9, off + 2)
        if not (box[0] <= x and x + UNIT <= box[2] and box[1] <= y
                and y + UNIT <= box[3]):
            continue
        if use_fx:
            if layer == 2 and sec9[off + 30] == 1:
                c = cell_of(off, sec9[off + 34])
                if c:
                    fx.append((off, c))
        else:
            c = cell_of(off, sec9[off + 32])
            if c:
                stat.append((layer, struct.unpack_from('<I', sec9,
                                                       off + 38)[0], off, c))
    if not fx or not stat:
        return [], st
    x0, y0 = box[0], box[1]
    W, H = (box[2] - x0) * scale, (box[3] - y0) * scale

    def blk(c):
        slot, cx, cy = c
        return np.frombuffer(pages[slot].data, '<u2').reshape(
            page_px, page_px)[cy:cy + step, cx:cx + step]

    def place(off):
        x, y = struct.unpack_from('<hh', sec9, off + 2)
        return (x - x0) * scale, (y - y0) * scale

    glow = np.zeros((H, W), bool)
    fxown = np.full((H, W), -1, np.int32)
    fxcnt = np.zeros((H, W), np.int16)
    for i, (o, c) in enumerate(fx):
        dx, dy = place(o)
        b = blk(c)
        sl = (slice(dy, dy + step), slice(dx, dx + step))
        glow[sl] |= (b != FN.EMPTY) & (_unpack(b).max(-1) >= 4)
        fxown[sl] = i
        fxcnt[sl] += 1
    # the visible static texel and which record draws it
    rgb = np.zeros((H, W, 3), np.float32)
    top = np.full((H, W), -1, np.int32)
    order = sorted(range(len(stat)), key=lambda k: (stat[k][0],
                                                    -stat[k][1]))
    for k in order:
        layer, _z, o, c = stat[k]
        dx, dy = place(o)
        b = blk(c)
        m = b != FN.EMPTY
        sl = (slice(dy, dy + step), slice(dx, dx + step))
        rgb[sl][m] = _unpack(b)[m]
        top[sl][m] = k
    # the bars: dark static slots under a keyed hole, inside the glow's hull
    hull = _morph(glow, 6 * scale, True)
    bar = (~glow) & (rgb.max(-1) < GRATE_DARK) & hull & (top >= 0)
    lab = np.zeros((H, W), np.int32)
    n = 0
    for sy, sx in zip(*np.nonzero(bar)):
        if lab[sy, sx]:
            continue
        n += 1
        stack = [(sy, sx)]
        lab[sy, sx] = n
        while stack:
            a, b_ = stack.pop()
            for da, db in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                c_, d_ = a + da, b_ + db
                if 0 <= c_ < H and 0 <= d_ < W and bar[c_, d_] \
                        and not lab[c_, d_]:
                    lab[c_, d_] = n
                    stack.append((c_, d_))
    face = np.zeros((H, W), np.int16)          # depth index 1..T, 0 = none
    facecov = np.zeros((H, W), np.float32)
    hole = ~glow
    half = int(1.5 * scale)
    for i in range(1, n + 1):
        ys, xs = np.nonzero(lab == i)
        w_, h_ = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
        if w_ < 8 * scale or h_ > 8 * scale or len(ys) < 4 * scale * scale:
            continue
        # the bar's axis through its dark core, then its full length along
        # that axis through the glow's hole (the dark core is broken up by
        # the art's own highlights)
        A = np.polyfit(xs.astype(float), ys.astype(float), 1)
        tops = {}
        for direction in (-1, 1):
            x = int(round(xs.mean()))
            while 0 <= x < W:
                yc = A[0] * x + A[1]
                lo, hi = int(yc) - half - scale, int(yc) + half + 1
                lo, hi = max(lo, 0), min(hi, H)
                colh = np.nonzero(hole[lo:hi, x])[0]
                if not len(colh) or len(colh) > (hi - lo) - 1:
                    break                     # glow again, or the post
                tops[x] = lo + colh.min()
                x += direction
        if len(tops) < 10 * scale:
            continue
        st['bars'] += 1
        xl = min(tops)
        kx = np.array(sorted(tops))
        raw = np.array([tops[x] for x in kx], float)
        # the bar is straight: fit its top edge (dropping the art's ragged
        # outliers) so the face has one clean line, anti-aliased below
        keep = np.ones(len(kx), bool)
        for _ in range(3):
            L = np.polyfit(kx[keep], raw[keep], 1)
            res = raw - np.polyval(L, kx)
            keep = np.abs(res) <= max(1.0, 1.5 * np.std(res[keep]))
        for x, yr in zip(kx, raw):
            yl = float(np.polyval(L, x))          # face bottom (bar top)
            t = min(GRATE_T, x - xl + 1)          # 45-degree bevel, left end
            ytop = yl - t
            bottom = max(int(np.ceil(yl)), int(yr))   # close gaps to the bar
            for yy in range(int(np.floor(ytop)), bottom):
                if not 0 <= yy < H:
                    continue
                cover = min(1.0, yy + 1 - ytop)       # partial top row
                if cover <= 0:
                    continue
                face[yy, x] = max(1, min(GRATE_T, int(round(yl - yy))))
                facecov[yy, x] = cover
    if not face.any():
        return [], st
    okc = {k: readers.get(stat[k][3]) == 1 for k in range(len(stat))}
    okf = np.array([readers.get(c) == 1 for _o, c in fx])
    plans = []
    lum = rgb.mean(-1)
    tex = np.clip(lum / max(1.0, float(np.median(lum[face > 0]))), 0.8, 1.2)
    wood = np.array(GRATE_WOOD, np.float32)
    for yy, xx in zip(*np.nonzero(face)):
        d = face[yy, xx]
        k = top[yy, xx]
        if k < 0 or not okc[k]:
            continue
        shade = 1.15 if d == 1 else 1.0 - 0.2 * (d - 1) / GRATE_T
        col = np.clip(wood * shade * (0.85 + 0.15 * tex[yy, xx]), 0, 255)
        cov = facecov[yy, xx]
        if cov < 0.999:                       # anti-aliased back edge
            col = cov * col + (1 - cov) * rgb[yy, xx]
        v = int(_pack(col[None])[0])
        if v == FN.EMPTY:
            v = FN.NEAR_BLACK
        layer, _z, o, (slot, cx, cy) = stat[k]
        dx, dy = place(o)
        plans.append((slot, cy + yy - dy, cx + xx - dx, v))
        i = fxown[yy, xx]
        if (i >= 0 and fxcnt[yy, xx] == 1 and okf[i] and glow[yy, xx]
                and cov >= 0.5):
            o2, (s2, cx2, cy2) = fx[i]
            dx2, dy2 = place(o2)
            plans.append((s2, cy2 + yy - dy2, cx2 + xx - dx2, FN.EMPTY))
    st['texels'] = len(plans)
    return plans, st


def apply_plans(sec9, plans):
    plist, tex_start, tex_end = FN.parse_texture_block(
        sec9, DC.parse_pages(sec9)[3])
    pages = {p.slot: p for p in plist if p is not None}
    arrays = {}
    for slot, py, px, v in plans:
        if slot not in arrays:
            p = pages[slot]
            arrays[slot] = np.frombuffer(p.data, '<u2').reshape(
                p.px, p.px).copy()
        arrays[slot][py, px] = v
    before = sec9[:tex_start] + sec9[tex_end:]
    for slot, arr in arrays.items():
        p = pages[slot]
        plist[slot] = FN.Page(slot, p.size_flag, p.depth, arr.tobytes(), p.px)
    out = FN.replace_texture_block(sec9, plist, tex_start, tex_end)
    _p, s2, e2 = FN.parse_texture_block(out, DC.parse_pages(out)[3])
    if out[:s2] + out[e2:] != before or len(out) != len(sec9):
        raise ValueError('non-texture bytes changed')
    return out


def apply_to_flevel(archive, payloads, art=None, encode=None,
                    log=lambda *_: None):
    import lgp
    total = {'fields': 0, 'texels': 0, 'cells': 0, 'names': [],
             'anim_fields': 0, 'anim_texels': 0, 'anim_names': [],
             'refused': []}
    if disabled() and anim_disabled():
        return total
    provider = getattr(art, 'provider', None)
    encode = encode or archive.encode_field
    for name in archive.names():
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            payload = payloads.get(name)
            raw = (lgp.lzs_decompress(payload[4:]) if payload
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            if parts[8].find(b'BACK') < 0:
                continue
            plans, st = ([], {'texels': 0, 'cells': 0}) if disabled() \
                else plan_section9(parts[8], name)
            if plans:
                parts[8] = apply_plans(parts[8], plans)
            ast = {'texels': 0}
            if provider is not None and not anim_disabled():
                import ff7nx_lostdetail as LD
                c9 = LD._cosmos_chunk9(provider, name)
                aplans, ast = plan_anim_clear(name, parts[8], c9, provider)
                if aplans:
                    parts[8] = apply_plans(parts[8], aplans)
                    plans = plans + aplans
            gplans, gst = plan_grate_faces(name, parts[8])
            if gplans:
                parts[8] = apply_plans(parts[8], gplans)
                plans = plans + gplans
                total['grate_texels'] = total.get('grate_texels', 0) + \
                    gst['texels']
                total.setdefault('grate_names', []).append(
                    '%s:%d bar(s)' % (name, gst['bars']))
            rplans, rst = plan_rim_despike(name, parts[8])
            if rplans:
                parts[8] = apply_plans(parts[8], rplans)
                plans = plans + rplans
                total['rim_texels'] = total.get('rim_texels', 0) + \
                    rst['texels']
                total.setdefault('rim_names', []).append(
                    '%s:%d' % (name, rst['texels']))
            cplans, cst = plan_anim_close(name, parts[8])
            if cplans:
                parts[8] = apply_plans(parts[8], cplans)
                plans = plans + cplans
                total['close_texels'] = total.get('close_texels', 0) + \
                    cst['texels']
                total.setdefault('close_names', []).append(
                    '%s:%d' % (name, cst['texels']))
            if not plans:
                continue
        except Exception as exc:                               # noqa: BLE001
            total['refused'].append(
                (name, '%s: %s' % (type(exc).__name__, str(exc)[:60])))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        if st['texels']:
            total['fields'] += 1
            total['texels'] += st['texels']
            total['cells'] += st['cells']
            total['names'].append('%s:%d' % (name, st['texels']))
        if ast['texels']:
            total['anim_fields'] += 1
            total['anim_texels'] += ast['texels']
            total['anim_names'].append('%s:%d' % (name, ast['texels']))
    return total


def summarise(st):
    out = []
    if st.get('anim_fields'):
        out.append('  ANIMATED OVERLAY EDGE (BUILD 617): %d texel(s) of 1997 '
                   'animated-overlay art keyed in %d field(s) (%s) where every '
                   'Cosmos image of the page is transparent and a static tile '
                   'behind covers it -- the overlay no longer laps over the HD '
                   'frame (mds5_1 screen). %s=1 disables.'
                   % (st['anim_texels'], st['anim_fields'],
                      ', '.join(st['anim_names'][:12]), ANIM_OFF_ENV))
    if st.get('grate_texels'):
        out.append('  GRATE TOP FACES (BUILD 617): %d texel(s) -- a flat '
                   'wooden top face painted above each grate bar (%s). '
                   '%s=1 disables.' % (st['grate_texels'],
                                      ', '.join(st['grate_names']),
                                      GRATE_OFF_ENV))
    if st.get('rim_texels'):
        out.append('  RIM DE-SPIKE (BUILD 617): %d over-bright texel(s) on '
                   'the cut edge of a static additive glow brought down to '
                   'the glow behind them (%s) -- no glowing seam along the '
                   'mds5_1 grate bars. %s=1 disables.'
                   % (st['rim_texels'], ', '.join(st['rim_names']),
                      RIM_OFF_ENV))
    if st.get('close_texels'):
        out.append('  ANIMATED OVERLAY EDGE CLOSE (BUILD 617): %d texel(s) of '
                   'small bays in an animated picture\'s outline closed with '
                   'its own neighbouring colour (%s) -- no static sliver '
                   'between picture and bezel. %s=1 disables.'
                   % (st['close_texels'], ', '.join(st['close_names']),
                      CLOSE_OFF_ENV))
    if st.get('refused'):
        out.append('  ! FX seam: %d field(s) unchanged (%s)' % (
            len(st['refused']), ', '.join('%s: %s' % r
                                         for r in st['refused'][:3])))
    if not st.get('fields'):
        return '\n'.join(out)
    return '\n'.join(out + [_seam_line(st)])


def _seam_line(st):
    return ('  FX SEAM (BUILD 617): %d additive texel(s) in %d cell(s) across '
            '%d field(s) carried up to the opaque cut-out they were masked to '
            '(%s) -- the ring of unlit background where a light fades out '
            'before a hard silhouette (trnad_2 rock tops). Only increases, '
            'only exclusive static additive cells. %s=1 disables.'
            % (st['texels'], st['cells'], st['fields'],
               ', '.join(st['names'][:12]), OFF_ENV))
