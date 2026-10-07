#!/usr/bin/env python3
"""ff7nx_trnad1fx.py -- trnad_1's lifestream transition copy, in HD and 16:9.

BUILD 618l. trnad_1 (Whirlwind Maze, the party wakes in the lifestream),
hardware 10-02: once when the cave fades out into the green haze and once
when it fades back in, a low-resolution copy of the field shows for about
half a second, without the widescreen sides.

MEASURED: layer 2 param 63 has two states.
  * State 1 is the cave: 466 records on the HD pages Cosmos widened to
    x -224..224.
  * State 2 is the transition copy: 840 FX records, blend modes 1 and 2, on
    the 256px paletted pages 15..18, mostly 4:3 width. The script switches
    63 to state 2 and ramps palettes 1/2/5/8 with ADPAL every frame (from
    dark to full or back), then switches to state 1.
On PC, FFNx follows the ramp by swapping Cosmos's 26 hash-named HD frames per
palette. The port can only fade a paletted page, and a paletted page is
always 256px. Cosmos never widened the copy's art.

FIX (the user's choice, 10-03: "HD, no fade"):
  * State 2 becomes an opaque HD copy of the state-1 cave, recoloured to
    match the transition copy at full strength.
  * The recolouring is a per-channel quadratic fitted by least squares from
    the cave (state 1, HD) to the copy as the port draws it at full strength
    (state 2 over the haze's mean colour) on every pixel where both exist.
    R^2 is about 0.9 on trnad_1, and refused below 0.75.
  * It covers the cave's whole 16:9 footprint. The copy appears at full
    strength instead of brightening in.
  * Every other record, the param-0 light shafts and every other layer are
    untouched.
  * Two depth-2 pages are added in free opaque slots. Page 18, which only
    the copy used, is dropped. Caps are checked.

ALSO: the field's remaining paletted FX pages, which hold only the param-0
light shafts, become depth-2 additive in place at their palettes' median
Cosmos pulse frame. The script pulsed palettes 6/7 with MPPAL every frame,
and that per-frame rewrite of paletted pages is the cost crater_2 showed
(hardware: the haze stepped at 30 fps for stretches). The pulse stops, as on
ancnt2 / las2_1 / crater_2.

SEVENTH_NX_NO_TRNAD1_FX=1 disables.
"""
from __future__ import annotations

import os
import struct

import numpy as np

import diag_common as DC
import field_bg_native as FN
import field_bg_repack as FR
import ff7nx_parallaxfill as PF

OFF_ENV = 'SEVENTH_NX_NO_TRNAD1_FX'
FIELDS = {'trnad_1': 63}
UV_CELL = 625000
GRID = 16
OPAQUE_SLOTS = list(range(1, 15)) + [26, 27, 28]
MIN_R2 = 0.75
ADD_PALETTE = 1
# BUILD 618n: OFF. 618l/m froze the light-shaft pages (15..17) HD at their
# median pulse frame. Hardware (10-03): while the field has vanished, a
# fixed shaft "watermark" stays in the upper centre. On vanilla (and the
# 618k build) the shafts' palettes 6/7 are driven by the script and do not
# show then -- a frozen truecolor page cannot follow that. The shafts stay
# paletted so the script controls them.
LIGHTS_HD = False


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _apply(x, w):
    """_feats(x) @ w without BLAS: np.einsum. numpy 2.x on macOS
    (Accelerate) raises spurious divide/overflow warnings from matmul on
    perfectly finite data (seen in the 618l build log); einsum does not go
    through BLAS. The result is checked to be finite either way."""
    out = np.einsum('nk,kc->nc', _feats(x), w)
    if not np.isfinite(out).all():
        raise ValueError('recolour produced non-finite values')
    return out


def _feats(x):
    x = x.reshape(-1, 3).astype(np.float64) / 255.0
    return np.concatenate([x, x * x, np.ones((len(x), 1))], 1)


def fit_recolour(parts, param):
    """(weights 7x3, r2 per channel) -- the state-1 -> state-2 recolouring."""
    import ff7nx_framesim as FS
    f = FS.Field(parts)
    haze = f.render(cam=(0, 0), layers=(3,), K=1, mark=False)
    bg = haze.reshape(-1, 3).astype(np.float64).mean(0)
    s2 = f.render(cam=(0, 0), states={param: 2}, layers=(2,), K=1,
                  mark=False, bg_color=bg).astype(np.float64)
    s1 = f.render(cam=(0, 0), states={param: 1}, layers=(2,), K=1, mark=True)
    base = ~(s1 == np.array(FS.MAGENTA, np.uint8)).all(-1)
    drawn = np.abs(s2 - bg).sum(-1) > 6
    m = base & drawn
    if m.sum() < 5000:
        raise ValueError('only %d overlapping pixels to fit' % m.sum())
    x, y = s1[m].astype(np.float64), s2[m]
    w = np.linalg.lstsq(_feats(x), y, rcond=None)[0]
    if not np.isfinite(w).all():
        raise ValueError('recolour fit is not finite')
    pred = _apply(x, w)
    r2 = 1 - ((y - pred) ** 2).sum(0) / ((y - y.mean(0)) ** 2).sum(0)
    return w, r2


def _light_pages(name, sec9, art, px, plist, skip=()):
    """Convert, IN PLACE, every paletted FX page still in use (after the copy
    is gone, only the param-0 light shafts) to a depth-2 additive page: each
    cell from its owning palette's Cosmos frames at that palette's median
    pulse level. Their MPPAL pulse (palettes 6/7, every frame) stops, as on
    ancnt2 / las2_1 / crater_2 -- and with it the last per-frame palette
    rewrite of a paletted page in this field. Returns the slots converted."""
    import collections
    import ff7nx_fxpages as FP
    import ff7nx_fxpcstatic as PS
    provider = art.provider
    _pl, ts, _te, _px = DC.parse_pages(sec9)
    refs = collections.defaultdict(list)
    for _layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in offs:
            if sec9[o + 28] and 15 <= sec9[o + 34] < 24:
                if sec9[o + 30] != 1:
                    raise ValueError('light tile blend %d' % sec9[o + 30])
                refs[sec9[o + 34]].append(o)
    done = []
    for slot, rs in sorted(refs.items()):
        if slot in skip:
            continue
        page = plist[slot]
        if page is None or page.depth != 1:
            continue
        owner = PS._owners(sec9, slot, page, rs)
        cs = px // (8 if page.size_flag else 16)
        img = np.zeros((px, px, 4), np.uint8)
        for q in sorted(set(owner.values())):
            recs = provider.state_slots.get((name.lower(), slot, q)) or ()
            if not recs:
                raise ValueError('page %d palette %d: no Cosmos art'
                                 % (slot, q))
            frames = []
            for rec in recs:
                im = PS._rec_rgba(provider, rec, px)
                a = im[..., 3:].astype(np.float32) / 255.0
                frames.append((float((im[..., :3] * a).mean()), rec[1], im))
            frames.sort(key=lambda t: (t[0], t[1]))
            sheet = frames[len(frames) // 2][2]
            for (cu, cv), qq in owner.items():
                if qq == q:
                    sl = (slice(cv * cs, (cv + 1) * cs),
                          slice(cu * cs, (cu + 1) * cs))
                    img[sl] = sheet[sl]
        plist[slot] = FN.Page(slot, page.size_flag, 2,
                              FP._encode_additive(img, px), px)
        done.append(slot)
    return done


def _kmeans(x, k, iters=12, seed=7):
    """Plain k-means on (n,3) float RGB; returns (k,3) centres."""
    rng = np.random.default_rng(seed)
    if len(x) > 60000:
        x = x[rng.choice(len(x), 60000, replace=False)]
    c = x[rng.choice(len(x), k, replace=len(x) < k)].copy()
    for _ in range(iters):
        d = ((x[:, None, :] - c[None, :, :]) ** 2).sum(-1)
        a = d.argmin(1)
        for j in range(k):
            m = a == j
            if m.any():
                c[j] = x[m].mean(0)
    return c


def _codes(rgb):
    q = np.clip(np.round(rgb / 255.0 * 31), 0, 31).astype(np.uint16)
    return (q[:, 0] | (q[:, 1] << 5) | (q[:, 2] << 10) | 0x8000).astype(
        np.uint16)


def plan_field(name, parts, param, mb_cap=None, raw_cap=None, art=None):
    """(new section 9, new section 3, info) -- raises on any doubt.

    BUILD 618m: the copy is rebuilt as what vanilla draws -- an ADDITIVE
    layer over the haze on palette 1, whose ramp (STPAL 1 -> ADPAL ->
    LDPAL 1/2, every frame) fades it from full strength to nothing and back.
    It covers the HD cave's full 16:9 footprint: each cell is the cave
    recoloured to the copy's look (the fitted quadratic), minus the haze's
    mean colour (an additive layer adds to the haze), at the paletted 1x
    density the fade needs. Palette 1 (used only by the copy) is a k-means
    palette of those colours; palette 2, which the script loads from the
    same buffer, gets the same colours.
    """
    import ff7nx_framesim as FS
    import ff7nx_marginblack as MB
    sec9 = parts[8]
    w, r2 = fit_recolour(parts, param)
    if not np.isfinite(r2).all() or float(r2.min()) < MIN_R2:
        raise ValueError('recolour fit too weak (R2 %s)' % np.round(r2, 2))
    f = FS.Field(parts)
    haze = f.render(cam=(0, 0), layers=(3,), K=1, mark=False)
    hmean = haze.reshape(-1, 3).astype(np.float64).mean(0)
    survey = DC.survey(sec9)
    rows = PF._layers(sec9, survey['back_start'], survey['tex_start'])
    hit = [r for r in rows if r[0] == 2]
    if len(hit) != 1:
        raise ValueError('expected one layer-2 array')
    _l, count_at, first, count = hit[0]
    recs = [sec9[first + i * 52:first + (i + 1) * 52] for i in range(count)]
    keep, base, old_add = [], [], []
    for r in recs:
        if r[26] == param and r[27] == 2:
            if r[28] and r[30] == 1 and r[22] == ADD_PALETTE:
                old_add.append(r)
            continue
        keep.append(r)
        if r[26] == param and r[27] == 1 and not r[28]:
            base.append(r)
    if not base or not old_add:
        raise ValueError('state 1 / additive state 2 records not found')
    template = old_add[0]
    zs = sorted(struct.unpack_from('<I', r, 38)[0] for r in old_add)
    z_add = zs[len(zs) // 2]
    # cells: HD cave -> copy colour -> minus haze, at 16 px per tile
    from PIL import Image
    cells, order = {}, []
    for r in base:
        key = (r[32], struct.unpack_from('<II', r, 42))
        if key in cells:
            continue
        o = first + recs.index(r) * 52
        blk = f._block(o, 2, 16)
        if blk is None:
            raise ValueError('base cell unreadable')
        rgb, k = blk
        col = np.clip(_apply(rgb, w), 0, 255).reshape(rgb.shape)
        add = np.clip(col - hmean, 0, 255)
        add[k] = 0
        small = np.asarray(Image.fromarray(add.astype(np.uint8)).resize(
            (16, 16), Image.BOX)).astype(np.float64)
        ks = np.asarray(Image.fromarray(k.astype(np.uint8) * 255).resize(
            (16, 16), Image.BOX)) > 127
        cells[key] = (small, ks)
        order.append(key)
    allpx = np.concatenate([c[~k] for c, k in cells.values()], 0)
    allpx = allpx[allpx.max(1) >= 8]
    centres = _kmeans(allpx, 255)
    codes = _codes(centres)
    centres_q = np.stack([(codes & 31), (codes >> 5) & 31,
                          (codes >> 10) & 31], -1).astype(np.float64) * 255 / 31
    # pages: free FX-band slots (depth 1, additive under blend mode 1)
    plist, _t0, _t1 = FN.parse_texture_block(sec9, f.px)
    present = {p.slot for p in plist if p is not None}
    used_after = set()
    for r in keep:
        used_after.add(r[32])
        if r[28]:
            used_after.add(r[34])
    for _layer, _c, fst, cnt in rows:
        if _layer == 2:
            continue
        for i in range(cnt):
            r = sec9[fst + i * 52:fst + (i + 1) * 52]
            used_after.add(r[32])
            if r[28]:
                used_after.add(r[34])
    drop = sorted(s for s in present if s not in used_after
                  and s in range(15, 24))
    need = -(-len(order) // (GRID * GRID))
    free = [s for s in range(15, 24) if s not in present or s in drop]
    if len(free) < need:
        raise ValueError('need %d FX-band slots, %d free' % (need, len(free)))
    slots = free[:need]
    pages = {s: np.zeros((256, 256), np.uint8) for s in slots}
    where = {}
    for i, key in enumerate(order):
        s = slots[i // (GRID * GRID)]
        n = i % (GRID * GRID)
        nx, ny = n % GRID, n // GRID
        small, ks = cells[key]
        d = ((small.reshape(-1, 1, 3) - centres_q[None]) ** 2).sum(-1)
        idx = (d.argmin(1) + 1).astype(np.uint8).reshape(16, 16)
        idx[ks | (small.max(-1) < 8)] = 0
        pages[s][ny * 16:(ny + 1) * 16, nx * 16:(nx + 1) * 16] = idx
        where[key] = (s, nx, ny)
    new2 = []
    for r in base:
        key = (r[32], struct.unpack_from('<II', r, 42))
        s, nx, ny = where[key]
        b = bytearray(template)
        b[2:6] = r[2:6]                        # destination x, y
        b[27] = 2
        b[22] = ADD_PALETTE
        b[28], b[30], b[34] = 1, 1, s
        struct.pack_into('<I', b, 38, z_add)
        struct.pack_into('<hh', b, 14, nx * 16, ny * 16)
        struct.pack_into('<hh', b, 10, nx * 16, ny * 16)
        struct.pack_into('<II', b, 42, nx * UV_CELL, ny * UV_CELL)
        new2.append(bytes(b))
    blob = b''.join(keep + new2)
    buf = bytearray(sec9)
    buf[first:first + count * 52] = blob
    struct.pack_into('<H', buf, count_at, len(keep) + len(new2))
    plist, t0, t1 = FN.parse_texture_block(bytes(buf), f.px)
    lights = []
    if art is not None and LIGHTS_HD:
        lights = _light_pages(name, bytes(buf), art, f.px, plist,
                              skip=set(slots))
    for s in drop:
        if s not in slots:
            plist[s] = None
    for s, idx in pages.items():
        plist[s] = FN.Page(s, 0, 1, idx.tobytes(), 256)
    run = sum(FR._page_bytes(p.px, p.depth) for p in plist if p is not None)
    if mb_cap is not None and run > mb_cap * 1048576.0:
        raise ValueError('%.2f MB over the %.0f MB cap'
                         % (run / 1048576.0, mb_cap))
    out = FN.replace_texture_block(bytes(buf), plist, t0, t1)
    # palettes 1 and 2
    sec3 = bytearray(parts[3])
    _cols, hdr, npg, cpp = MB.palette_colours(bytes(sec3))
    if cpp != 256 or npg <= 2:
        raise ValueError('palette layout %dx%d' % (npg, cpp))
    for pal in (1, 2):
        poff = hdr + 2 * pal * cpp
        struct.pack_into('<H', sec3, poff, 0)
        for j, v in enumerate(codes, 1):
            struct.pack_into('<H', sec3, poff + 2 * j, int(v))
    other = sum(len(x) for x in parts) - len(sec9)
    if raw_cap is not None and other + len(out) > raw_cap:
        raise ValueError('raw %d over cap %d' % (other + len(out), raw_cap))
    import field_bg_pagecap as PC
    counts = PC.effective_counts(out, f.px)
    over = {k: v for k, v in counts.items() if v > PC.MAX_TILES_PER_PAGE}
    if over:
        raise ValueError('binding-page cap: %s' % over)
    return out, bytes(sec3), {
        'lights': lights, 'removed': count - len(keep), 'added': len(new2),
        'cells': len(order), 'slots': slots,
        'dropped': [s for s in drop if s not in slots],
        'r2': [round(float(v), 3) for v in r2],
        'mb': round(run / 1048576.0, 2)}


def apply_to_flevel(archive, payloads, encode=None, mb_cap=None,
                    raw_cap=None, art=None, log=lambda *_: None):
    import lgp
    total = {'names': [], 'refused': []}
    if disabled():
        return total
    encode = encode or archive.encode_field
    for name, param in FIELDS.items():
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            parts[8], parts[3], info = plan_field(
                name, parts, param, mb_cap=mb_cap, raw_cap=raw_cap, art=art)
        except Exception as exc:                               # noqa: BLE001
            total['refused'].append((name, str(exc)[:90]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        total['names'].append('%s: %d FX copy records -> %d 16:9 additive records on '
                              'pages %s (fit R2 %s, %.2f MB); light pages %s HD'
                              % (name, info['removed'], info['added'],
                                 info['slots'], info['r2'], info['mb'],
                                 info['lights']))
    return total


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  TRNAD_1 TRANSITION (BUILD 618m): the lifestream fade '
                   'copy rebuilt over the full 16:9 cave on its own ramped '
                   'palette, so it fades out and back like vanilla (%s). '
                   '%s=1 disables.'
                   % ('; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! trnad_1 transition %s: %s' % (name, why))
    return '\n'.join(out)
