#!/usr/bin/env python3
"""ff7nx_laserhd.py -- straight 1x laser beams redrawn as clean HD lines.
BUILD 620k3.

semkin_4 (hardware 10-07): "the lasers in semkin_4 look a bit low quality
... lots of dots, rough edges along the path of the line ... are they true
color?"

THE BEAMS. Three red beams (a V and a single one) are static additive
records on layer 2 (palette 9, blend mode 1, param 0) on the PALETTED FX
pages 15/16 -- 1997 1x art, so each beam is a staircase of 1x pixels. The
field's `light` entity also rotates palette 9 every frame (RTPAL over the
beam's entries): that is the crawl of dots along each beam.

WHAT THIS DOES (FIELDS only):
  * the beam records are composed at 1x in field space;
  * each beam is found as a straight line (Hough on the lit pixels, then a
    total-least-squares fit of the pixels nearest each peak) with its
    extent;
  * its brightness along its length is the 1x cross-section sum, smoothed
    along the beam (so the rotating-palette dots average out) and kept per
    channel, so the colour and the fade at the ends stay the 1997 beam's;
  * it is redrawn at 3x as an anti-aliased line: a Gaussian cross-section
    (CORE units) carrying that same light per unit of length, plus a faint
    wider halo (HALO, HALO_SHARE of the light);
  * every beam record is moved to a free cell of a TRUECOLOR additive FX
    page (slots 15..23) with its own HD cell; the old paletted cells are
    left unread. Record count, positions, depth and blend are unchanged.

BUILD 620k4 (hardware 10-08: "goes from thin to thick (which is good) but
then back to thin near the ends"): the 1997 beam brightens and dims in
patches along its path, which at a fixed width reads as thickness. The
light along each beam is now the best MONOTONE fit of that profile (rising
or falling, whichever fits), one colour, with a soft END_FADE at the art's
two ends.

WHAT IT COSTS. The crawl of dots (palette 9's rotation) no longer shows on
the beams: they are steady, at the rotation's average brightness.
SEVENTH_NX_NO_LASER_HD=1 keeps the 1x beams.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os
import struct

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_LASER_HD'
# field -> palettes of the beams
FIELDS = {'semkin_4': (9,)}
K = 3
UV = 10_000_000
UV_CELL = UV // 16
CORE = 0.45            # units: sigma of the beam's core
HALO = 1.4             # units: sigma of its glow
HALO_SHARE = 0.25      # of the light
ALONG = 6.0            # units: smoothing of the brightness along the beam
MIN_VOTES = 40         # 1x pixels on a beam
MAX_BEAMS = 6
FIT_DIST = 1.5         # units: pixels this close to a peak line fit it
MONOTONE = True        # BUILD 620k4: light along a beam never dips back
END_FADE = 3.0         # units: soft fade at each end of the art


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _hough(mask, w):
    """[(theta, rho)] strongest straight lines through the lit pixels."""
    ys, xs = np.nonzero(mask)
    if len(xs) < MIN_VOTES:
        return []
    thetas = np.deg2rad(np.arange(0, 180, 0.5))
    ct, st = np.cos(thetas), np.sin(thetas)
    diag = int(np.ceil(np.hypot(*mask.shape))) + 1
    acc = np.zeros((len(thetas), 2 * diag + 1), np.float64)
    wt = w[ys, xs]
    for i in range(len(thetas)):
        r = np.round(xs * ct[i] + ys * st[i]).astype(int) + diag
        np.add.at(acc[i], r, wt)
    lines = []
    for _ in range(MAX_BEAMS):
        i, j = np.unravel_index(acc.argmax(), acc.shape)
        votes = acc[i, j]
        th, rho = thetas[i], j - diag
        d = np.abs(xs * np.cos(th) + ys * np.sin(th) - rho)
        n = int((d <= FIT_DIST).sum())
        if n < MIN_VOTES:
            break
        lines.append((th, rho))
        # suppress this line's neighbourhood
        acc[max(0, i - 12):i + 13, max(0, j - 4):j + 5] = 0
        if votes <= 0:
            break
    return lines


def _fit(xs, ys, w):
    """(point, unit direction) of the weighted total-least-squares line."""
    c = np.array([np.average(xs, weights=w), np.average(ys, weights=w)])
    d = np.stack([xs - c[0], ys - c[1]], 1)
    cov = (d * w[:, None]).T @ d
    ev, evec = np.linalg.eigh(cov)
    u = evec[:, 1]
    return c, u / np.linalg.norm(u)


def beams(img):
    """[(point, direction, t0, t1, profile(t) -> rgb per unit)] of the 1x
    additive image `img` (H, W, 3), pixel centres at +0.5."""
    lum = img.max(-1)
    mask = lum > 0
    lines = _hough(mask, lum)
    ys, xs = np.nonzero(mask)
    px = xs + 0.5
    py = ys + 0.5
    rgb = img[ys, xs]
    if not lines:
        return []
    # each pixel goes to its nearest peak line
    dist = np.stack([np.abs(px * np.cos(th) + py * np.sin(th) - rho - 0.5 *
                            (np.cos(th) + np.sin(th)))
                     for th, rho in lines], 1)
    own = dist.argmin(1)
    out = []
    for k_ in range(len(lines)):
        sel = (own == k_) & (dist[np.arange(len(own)), own] <= FIT_DIST + 1)
        if sel.sum() < MIN_VOTES:
            continue
        c, u = _fit(px[sel], py[sel], rgb[sel].max(-1) + 1.0)
        t = (px[sel] - c[0]) * u[0] + (py[sel] - c[1]) * u[1]
        n = (px[sel] - c[0]) * -u[1] + (py[sel] - c[1]) * u[0]
        if np.abs(n).max() > FIT_DIST + 1.5:
            sel_ = np.abs(n) <= FIT_DIST + 1
        else:
            sel_ = np.ones_like(n, bool)
        t, col = t[sel_], rgb[sel][sel_]
        t0, t1 = float(t.min()) - 0.5, float(t.max()) + 0.5
        nb = int(np.ceil(t1 - t0)) + 1
        bins = np.clip(np.floor(t - t0).astype(int), 0, nb - 1)
        acc = np.zeros((nb, 3))
        np.add.at(acc, bins, col)
        # light per unit of length: the sum across the beam, per bin, then
        # smoothed along it (normalised so the ends keep their fade)
        from scipy.ndimage import gaussian_filter1d
        cover = np.zeros(nb)
        np.add.at(cover, bins, 1.0)
        has = (cover > 0).astype(float)
        sm = np.stack([gaussian_filter1d(acc[:, ch], ALONG, mode='constant')
                       for ch in range(3)], 1)
        hs = gaussian_filter1d(has, ALONG, mode='constant')
        prof = np.where(hs[:, None] > 1e-3, sm / np.maximum(hs, 1e-3)[:, None],
                        0.0) * (has[:, None] > 0)
        if MONOTONE:
            prof = _monotone(prof, has > 0)
        out.append((c, u, t0, t1, prof))
    return out


def _pav(y, w):
    """Weighted non-decreasing least-squares fit (pool adjacent
    violators)."""
    vals, wts, cnt = [], [], []
    for yi, wi in zip(y, w):
        vals.append(yi)
        wts.append(wi)
        cnt.append(1)
        while len(vals) > 1 and vals[-2] > vals[-1]:
            wsum = wts[-2] + wts[-1]
            v = (vals[-2] * wts[-2] + vals[-1] * wts[-1]) / max(wsum, 1e-9)
            c_ = cnt[-2] + cnt[-1]
            vals[-2:] = [v]
            wts[-2:] = [wsum]
            cnt[-2:] = [c_]
    return np.repeat(vals, cnt)


def _monotone(prof, lit):
    """BUILD 620k4. The beam's light along its length made monotone (the
    better of rising or falling), per channel by one shared shape: the
    1997 beam brightens and dims along its path in patches, which drawn at
    a fixed width read as a beam going thin -> thick -> thin again
    (hardware 10-08: "goes from thin to thick (which is good) but then
    back to thin near the ends"). The fade over the last END_FADE units
    of the art is kept."""
    lum = prof.max(1)
    w = lit.astype(float) + 1e-3
    up = _pav(lum, w)
    dn = _pav(lum[::-1], w[::-1])[::-1]
    fit = up if ((up - lum) ** 2 * w).sum() <= ((dn - lum) ** 2 * w).sum() \
        else dn
    # the beam's colour: light-weighted mean chromaticity
    ch = (prof * lit[:, None]).sum(0) / max(float((lum * lit).sum()), 1e-6)
    out = fit[:, None] * ch[None, :]
    idx = np.nonzero(lit)[0]
    if len(idx):
        a, b = idx[0], idx[-1]
        n = len(prof)
        ramp = np.clip(np.minimum(np.arange(n) - a, b - np.arange(n)) /
                       END_FADE, 0, 1)
        out = out * (0.35 + 0.65 * ramp)[:, None]
        out[:a] = 0
        out[b + 1:] = 0
    return out


def render(bs, shape):
    """HD (3x) additive image of the beams over a field canvas of `shape`
    (1x H, W)."""
    H, W = shape
    out = np.zeros((H * K, W * K, 3), np.float64)
    yy, xx = np.mgrid[0:H * K, 0:W * K]
    fx = (xx + 0.5) / K
    fy = (yy + 0.5) / K
    g1 = 1.0 / (np.sqrt(2 * np.pi) * CORE)
    g2 = 1.0 / (np.sqrt(2 * np.pi) * HALO)
    for c, u, t0, t1, prof in bs:
        t = (fx - c[0]) * u[0] + (fy - c[1]) * u[1]
        n = (fx - c[0]) * -u[1] + (fy - c[1]) * u[0]
        near = (np.abs(n) < 4 * HALO) & (t > t0 - 1) & (t < t1 + 1)
        if not near.any():
            continue
        tt, nn = t[near], n[near]
        idx = np.clip(np.floor(tt - t0).astype(int), 0, len(prof) - 1)
        # soft ends: half a unit
        endw = np.clip(np.minimum(tt - t0, t1 - tt) + 0.5, 0, 1)
        prof_ = prof[idx] * endw[:, None]
        cross = ((1 - HALO_SHARE) * g1 * np.exp(-0.5 * (nn / CORE) ** 2)
                 + HALO_SHARE * g2 * np.exp(-0.5 * (nn / HALO) ** 2))
        out[near] += prof_ * cross[:, None]
    return np.clip(out, 0, 255)


def _enc565(cell):
    q = np.clip(np.floor(cell / 8.0 + 0.5), 0, 31).astype(np.uint32)
    v = (q[..., 0] << 11 | (q[..., 1] << 1) << 5 | q[..., 2]).astype(np.uint16)
    v[q.max(-1) == 0] = 0
    return v


def plan_field(name, parts):
    """(new section 9, info) -- raises on any doubt."""
    import diag_common as DC
    import field_bg_native as FN
    import ff7nx_marginblack as MB
    sec9 = bytes(parts[8])
    pl, ts, _te, px = DC.parse_pages(sec9)
    pm = {p.slot: p for p in pl if p is not None}
    k = px // 16
    if k != 16 * K:
        raise ValueError('pages are %d px' % px)
    cols, _h, npg, cpp = MB.palette_colours(parts[3])
    pal = cols.astype(np.int64).reshape(npg, cpp)
    used = set()
    recs = []
    for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in offs:
            bl = sec9[o + 28]
            n = struct.unpack_from('<H', sec9, o + 18)[0] or 16
            for po, so in ((32, 10), (34, 14)):
                if po == 34 and not bl:
                    continue
                sx, sy = struct.unpack_from('<hh', sec9, o + so)
                pg = sec9[o + po]
                p = pm.get(pg)
                g = 16 if (p is not None and p.depth == 2) else 16
                for cy in range(sy // 16, (sy + n - 1) // 16 + 1):
                    for cx in range(sx // 16, (sx + n - 1) // 16 + 1):
                        used.add((pg, cx, cy))
            if (layer == 2 and bl and sec9[o + 30] == 1 and not sec9[o + 26]
                    and sec9[o + 22] in FIELDS[name]
                    and pm.get(sec9[o + 34]) is not None
                    and pm[sec9[o + 34]].depth == 1):
                if struct.unpack_from('<HH', sec9, o + 18) != (16, 16):
                    raise ValueError('beam tile is not 16x16')
                recs.append(o)
    if not recs:
        raise ValueError('no paletted beam records')
    xs = [struct.unpack_from('<h', sec9, o + 2)[0] for o in recs]
    ys = [struct.unpack_from('<h', sec9, o + 4)[0] for o in recs]
    X0, Y0 = min(xs), min(ys)
    W1, H1 = max(xs) + 16 - X0, max(ys) + 16 - Y0
    img = np.zeros((H1, W1, 3), np.float64)
    for o in recs:
        x, y = struct.unpack_from('<hh', sec9, o + 2)
        sx, sy = struct.unpack_from('<hh', sec9, o + 14)
        a = np.frombuffer(pm[sec9[o + 34]].data, np.uint8, count=65536
                          ).reshape(256, 256)[sy:sy + 16, sx:sx + 16]
        c = pal[sec9[o + 22]][a]
        lit = (c & 0x7FFF) != 0
        rgb = np.stack([c & 31, (c >> 5) & 31, (c >> 10) & 31], -1) * 8.0
        img[y - Y0:y - Y0 + 16, x - X0:x - X0 + 16] += np.where(
            lit[..., None], rgb, 0.0)
    bs = beams(img)
    if not bs:
        raise ValueError('no straight beam found')
    lit_in = img.sum()
    hd = render(bs, (H1, W1))
    lit_out = hd.sum() / (K * K)
    if not 0.6 < lit_out / max(lit_in, 1.0) < 1.4:
        raise ValueError('redrawn beams carry %.2fx the light'
                         % (lit_out / max(lit_in, 1.0)))
    # free cells on truecolor additive slots 15..23
    free = []
    for slot in range(0x0F, 0x18):
        p = pm.get(slot)
        if p is None or p.depth != 2 or p.size_flag:
            continue
        for cy in range(16):
            for cx in range(16):
                if (slot, cx, cy) not in used:
                    free.append((slot, cx, cy))
    if len(free) < len(recs):
        raise ValueError('%d free truecolor FX cells for %d beam records'
                         % (len(free), len(recs)))
    datas = {}
    out = bytearray(sec9)
    for o, (slot, cx, cy) in zip(recs, free):
        x, y = struct.unpack_from('<hh', sec9, o + 2)
        Y, X = (y - Y0) * K, (x - X0) * K
        cell = _enc565(hd[Y:Y + k, X:X + k])
        if slot not in datas:
            datas[slot] = np.frombuffer(pm[slot].data, '<u2').reshape(
                px, px).copy()
        datas[slot][cy * k:(cy + 1) * k, cx * k:(cx + 1) * k] = cell
        out[o + 34] = slot
        struct.pack_into('<hh', out, o + 14, cx * 16, cy * 16)
        struct.pack_into('<II', out, o + 42, cx * UV_CELL, cy * UV_CELL)
    out = bytes(out)
    plist, t0, t1 = FN.parse_texture_block(out, px)
    for s_, d in datas.items():
        q_ = plist[s_]
        plist[s_] = FN.Page(s_, q_.size_flag, 2, d.tobytes(), px)
    new = FN.replace_texture_block(out, plist, t0, t1)
    return new, {'records': len(recs), 'beams': len(bs),
                 'light': round(lit_out / max(lit_in, 1.0), 3),
                 'slots': sorted({f[0] for f in free[:len(recs)]})}


def apply_to_flevel(archive, payloads, encode=None, log=lambda *_: None):
    import lgp
    st = {'names': [], 'refused': []}
    if disabled():
        return st
    encode = encode or archive.encode_field
    for name in FIELDS:
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            continue
        try:
            p = payloads.get(name)
            raw = (lgp.lzs_decompress(p[4:]) if p
                   else archive.decompressed(entry))
            parts = list(lgp.split_sections(raw))
            parts[8], info = plan_field(name, parts)
        except Exception as exc:                               # noqa: BLE001
            st['refused'].append((name, str(exc)[:100]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        st['names'].append('%s: %d beam records, %d beams, light x%.2f, '
                           'slots %s' % (name, info['records'], info['beams'],
                                         info['light'],
                                         ','.join(map(str, info['slots']))))
    return st


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  LASER HD (BUILD 620k3): 1x laser beams redrawn as clean '
                   'anti-aliased HD lines (%s). %s=1 disables.'
                   % ('; '.join(st['names']), OFF_ENV))
    for name, why in st.get('refused', ()):
        out.append('  ! laser hd %s: %s' % (name, why))
    return '\n'.join(out)
