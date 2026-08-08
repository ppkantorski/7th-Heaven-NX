#!/usr/bin/env python3
"""
locate_field.py -- name the field in a console capture, and say WHERE the
camera was.

HANDOFF-66 §6 step 2. The captures showing the black rectangles were never
identified; §5's lead cannot be confirmed or dropped until they are.

WHY match_screenshot.py's CENTRE CROP WAS NOT ENOUGH -- MEASURED
===============================================================
It scored a re-rendered field against itself at 1.000 and the runner-up at
0.25, so the pooling and the correlation were right. Against the real
captures it scored 0.24-0.28 -- the runner-up's number. The template was
correct and the SEARCH was wrong:

    the 4:3 window is 320x224 of a background that is usually much larger,
    and the camera pans. A crop centred on the field's origin is the right
    picture in the wrong place.

So this slides. The capture is the template; every 4:3 window of every
field's rendered background is a candidate.

GEOMETRY, MEASURED
==================
`2026-08-04_09-27-28.jpg`: the side bands are pure green, so the edge of the
4:3 picture is visible to the pixel -- x 160..1120, y 24..696 of 1280x720.
960x672 is the 320x224 picture at exactly 3x, centred, with 24px of true
letterbox above and below.

SCORE
=====
Normalised cross-correlation of the LUMA GRADIENT, computed by FFT over
every window position at once. Gradient rather than colour because the
console applies its own gamma, the capture is JPEG, and a field whose bands
are green has a cast the archive does not -- but edges survive all three.

Field models, dialogue boxes and the black rectangles are foreground the
archive does not contain, so a true match does not reach 1.0. The GAP to the
runner-up is the evidence, and `--sanity` re-runs the self-test that caught
the centre-crop error.
"""
from __future__ import annotations

import os
import struct
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import lgp                                                      # noqa: E402
import diag_common as DC                                        # noqa: E402
import render_fields as RF                                      # noqa: E402

# a canvas wide enough for the widest background in the archive
W = H = 1536
CX = CY = 768
PIC_W, PIC_H = 320, 224
POOL = 8                              # dst pixels per pooled cell
PIC_1280 = (160, 24, 960, 672)        # the 4:3 picture inside a 1280x720 grab

TILE = 16
T_DSTX, T_DSTY, T_SRCX, T_SRCY, T_PAL, T_TEX = 2, 4, 10, 12, 22, 32


def render_big(raw, layers=(1, 2)):
    """
    The whole background on a canvas large enough not to clip it.

    render_fields.render draws on 640x480 and drops any tile that does not
    fit, which silently truncates every field wider than 640 -- and the ones
    that pan are exactly the wide ones. Same reader, bigger canvas.
    """
    parts = lgp.split_sections(raw)
    pal, cpp, npg = RF.palettes(parts[3])
    sec9 = parts[8]
    surv = DC.survey(sec9)
    pages = {p.slot: p for p in surv['pages']}
    cache = {}
    img = np.zeros((H, W, 3), np.uint8)
    drawn = np.zeros((H, W), bool)
    for layer, offs in DC.walk_layers(sec9, surv['back_start'],
                                      surv['tex_start']):
        if layer not in layers:
            continue
        for o in offs:
            dx = struct.unpack_from('<h', sec9, o + T_DSTX)[0] + CX
            dy = struct.unpack_from('<h', sec9, o + T_DSTY)[0] + CY
            if not (0 <= dx <= W - TILE and 0 <= dy <= H - TILE):
                continue
            slot = sec9[o + T_TEX]
            p = pages.get(slot)
            if p is None:
                continue
            if slot not in cache:
                cache[slot] = RF.page_rgb(p)
            data, opaque = cache[slot]
            sx, sy = sec9[o + T_SRCX], sec9[o + T_SRCY]
            if p.depth == 1:
                idx = data[sy:sy + TILE, sx:sx + TILE]
                if idx.shape != (TILE, TILE):
                    continue
                pid = sec9[o + T_PAL]
                if pid >= npg:
                    continue
                rgb = pal[pid][idx]
                m = idx != 0
            else:
                k = p.px // 256
                rgb = data[sy * k:sy * k + TILE * k, sx * k:sx * k + TILE * k]
                if rgb.shape[:2] != (TILE * k, TILE * k):
                    continue
                m = opaque[sy * k:sy * k + TILE * k, sx * k:sx * k + TILE * k]
                rgb, m = rgb[::k, ::k], m[::k, ::k]
            sub = img[dy:dy + TILE, dx:dx + TILE]
            sub[m] = rgb[m]
            drawn[dy:dy + TILE, dx:dx + TILE] |= m
    return img, drawn


def luma(img):
    return img.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)


def pooled_grad(l, pool=POOL):
    """Pool luma by `pool`, then its gradient, as one stacked feature plane."""
    h, w = l.shape
    l = l[:h // pool * pool, :w // pool * pool]
    p = l.reshape(h // pool, pool, w // pool, pool).mean(axis=(1, 3))
    gx = np.zeros_like(p)
    gy = np.zeros_like(p)
    gx[:, :-1] = np.diff(p, axis=1)
    gy[:-1, :] = np.diff(p, axis=0)
    return np.stack([gx, gy])


def ncc_map(hay, needle):
    """
    Normalised cross-correlation of `needle` over every window of `hay`,
    for a stack of feature planes. Returns the best value and its position.
    """
    from numpy.fft import rfft2, irfft2
    C, Hh, Ww = hay.shape
    _, hn, wn = needle.shape
    if Hh < hn or Ww < wn:
        return -1.0, (0, 0)
    n = needle - needle.mean(axis=(1, 2), keepdims=True)
    nn = np.linalg.norm(n)
    if nn == 0:
        return -1.0, (0, 0)
    shape = (Hh, Ww)
    num = np.zeros((Hh - hn + 1, Ww - wn + 1))
    # sum over channels of correlate(hay, n)
    ones = np.ones((hn, wn))
    s1 = np.zeros_like(num)
    s2 = np.zeros_like(num)
    for c in range(C):
        Fh = rfft2(hay[c], shape)
        num += irfft2(Fh * np.conj(rfft2(n[c], shape)),
                      shape)[:Hh - hn + 1, :Ww - wn + 1]
        s1 += irfft2(Fh * np.conj(rfft2(ones, shape)),
                     shape)[:Hh - hn + 1, :Ww - wn + 1]
        s2 += irfft2(rfft2(hay[c] ** 2, shape) * np.conj(rfft2(ones, shape)),
                     shape)[:Hh - hn + 1, :Ww - wn + 1]
    var = s2 - s1 ** 2 / (C * hn * wn)
    den = np.sqrt(np.maximum(var, 1e-9)) * nn
    r = num / den
    i = int(np.argmax(r))
    return float(r.flat[i]), np.unravel_index(i, r.shape)


def capture_template(path):
    from PIL import Image
    img = np.asarray(Image.open(path).convert('RGB'))
    Hc, Wc = img.shape[:2]
    s = Wc / 1280.0
    x, y, w, h = (int(round(v * s)) for v in PIC_1280)
    crop = img[y:y + h, x:x + w]
    small = np.asarray(Image.fromarray(crop).resize((PIC_W, PIC_H),
                                                    Image.BILINEAR))
    return small, (Wc, Hc)


def scan(flevel, shots, top=6, fields=None, layers=(1, 2), progress=True):
    arc = lgp.Archive(flevel)
    names = sorted(n for n in arc.names() if arc.is_field(arc.index[n]))
    if fields:
        names = [n for n in names if n in fields]
    tmpl = {}
    for s in shots:
        small, dims = capture_template(s)
        tmpl[s] = (pooled_grad(luma(small)), dims)
    results = {s: [] for s in shots}
    bad = {}
    for k, nm in enumerate(names):
        try:
            img, drawn = render_big(arc.decompressed(arc.index[nm]), layers)
        except Exception as exc:                                # noqa: BLE001
            bad[nm] = '%s: %s' % (type(exc).__name__, str(exc)[:40])
            continue
        ys, xs = np.nonzero(drawn)
        if not ys.size:
            continue
        y0, y1 = max(0, ys.min() - 8), min(H, ys.max() + 9)
        x0, x1 = max(0, xs.min() - 8), min(W, xs.max() + 9)
        hay = pooled_grad(luma(img[y0:y1, x0:x1]))
        for s, (t, _d) in tmpl.items():
            r, (py, px) = ncc_map(hay, t)
            results[s].append((r, nm, (x0 + px * POOL - CX,
                                       y0 + py * POOL - CY)))
        if progress and k % 100 == 0:
            print('   ... %d/%d' % (k, len(names)), file=sys.stderr)
    for s in results:
        results[s].sort(reverse=True)
        results[s] = results[s][:top]
    return results, bad


def sanity(flevel, hay, fields=('mds7st3', 'mrkt4', 'nmkin_1')):
    """
    Re-render a field, letterbox it exactly like a capture, and look for it.

    This is the test that caught the centre-crop error: a self-match must
    come back at rank 1 with a large gap. If it does not, the geometry or the
    pooling is wrong and no result below is worth reading.
    """
    from PIL import Image
    arc = lgp.Archive(flevel)
    ok = True
    for nm in fields:
        img, drawn = render_big(arc.decompressed(arc.index[nm]))
        ys, xs = np.nonzero(drawn)
        cy = int((ys.min() + ys.max()) // 2)
        cx = int((xs.min() + xs.max()) // 2)
        win = img[cy - PIC_H // 2:cy + PIC_H // 2,
                  cx - PIC_W // 2:cx + PIC_W // 2]
        big = np.array(Image.fromarray(win).resize((960, 672), Image.BILINEAR))
        canvas = np.zeros((720, 1280, 3), np.uint8)
        canvas[24:696, 160:1120] = big
        p = '/tmp/sanity_%s.jpg' % nm
        Image.fromarray(canvas).save(p, quality=88)
        hits, _d, _f = search(p, hay, top=3)
        got = hits[0][1] if hits else '-'
        print('  %-10s -> %s' % (nm, ['%s %.3f' % (n, r) for r, n, _ in hits]))
        ok &= (got == nm)
    print('  SANITY %s' % ('PASS' if ok else 'FAIL'))
    return ok


# --------------------------------------------------------- masked NCC
def masked_ncc(hay, needle, mask):
    """
    Padfield's masked normalised cross-correlation.

    The captures showing the black rectangles have 20-30% of the picture
    replaced by the defect itself, and that is signal the archive cannot
    contain. Correlating over it drags every candidate towards the same
    number -- which is exactly what the 0.03-0.05 rank1/rank2 gaps were
    before this existed. Excluding those cells asks the right question:
    does the part of the capture that IS background agree with this field?

    Measured, on `2026-08-04_09-26-50.jpg`: unmasked, mds6_2 scored 0.329
    with a 0.049 gap -- indistinguishable from noise. Masked, 0.548 with a
    0.260 gap, and rank 2 is mds6_22, the same scene's twin.

    `mask` is 1 where the capture is trustworthy, 0 where it is not.
    """
    from numpy.fft import rfft2, irfft2
    C, Hh, Ww = hay.shape
    _, hn, wn = needle.shape
    if Hh < hn or Ww < wn:
        return -1.0, (0, 0)
    m = np.broadcast_to(mask, needle.shape).astype(np.float64)
    t = needle * m
    shape = (Hh, Ww)
    sl = (slice(None, Hh - hn + 1), slice(None, Ww - wn + 1))
    n_m = m.sum()
    if n_m < 16:
        return -1.0, (0, 0)
    sum_ft = np.zeros((Hh - hn + 1, Ww - wn + 1))
    sum_f = np.zeros_like(sum_ft)
    sum_f2 = np.zeros_like(sum_ft)
    for c in range(C):
        F = rfft2(hay[c], shape)
        F2 = rfft2(hay[c] ** 2, shape)
        M = np.conj(rfft2(m[c], shape))
        sum_ft += irfft2(F * np.conj(rfft2(t[c], shape)), shape)[sl]
        sum_f += irfft2(F * M, shape)[sl]
        sum_f2 += irfft2(F2 * M, shape)[sl]
    sum_t = t.sum()
    sum_t2 = (needle ** 2 * m).sum()
    num = sum_ft - sum_f * sum_t / n_m
    var_f = np.maximum(sum_f2 - sum_f ** 2 / n_m, 1e-9)
    var_t = max(sum_t2 - sum_t ** 2 / n_m, 1e-9)
    r = num / np.sqrt(var_f * var_t)
    i = int(np.argmax(r))
    return float(r.flat[i]), np.unravel_index(i, r.shape)


def capture_mask(path, pool=POOL, black=14, frac=0.35):
    """
    Pooled 1/0 mask of the capture: 0 where a cell is mostly PURE BLACK.

    Pure black is what the defect paints. Real scenery in these fields is
    dark but not zero -- `mrkt4`'s own filler is RGB(0, 0, 8) and the darkest
    shadows in the captures sit well above 14. The threshold is deliberately
    low so that dark ART is kept and only the defect is dropped.
    """
    from PIL import Image
    img = np.asarray(Image.open(path).convert('RGB'))
    Hc, Wc = img.shape[:2]
    s = Wc / 1280.0
    x, y, w, h = (int(round(v * s)) for v in PIC_1280)
    crop = np.asarray(Image.fromarray(img[y:y + h, x:x + w])
                      .resize((PIC_W, PIC_H), Image.BILINEAR))
    dark = (crop.max(axis=2) <= black)
    g = dark[:PIC_H // pool * pool, :PIC_W // pool * pool]
    g = g.reshape(PIC_H // pool, pool, PIC_W // pool, pool).mean(axis=(1, 3))
    return (g < frac).astype(np.float64)[None], float((g >= frac).mean())


def load_cache(cachedir):
    import glob
    out = []
    for p in sorted(glob.glob(os.path.join(cachedir, 'hay_*.npz'))):
        z = np.load(p, allow_pickle=True)
        names = [str(x) for x in z['names']]
        orig = z['origins']
        for i, nm in enumerate(names):
            out.append((nm, z['p%d' % i], orig[i]))
    return out


def build_cache(flevel, cachedir, chunk=250, layers=(1, 2)):
    """Cache every field's pooled luma-gradient plane. Rendering all 711 is
    ~50s; searching the cache is milliseconds, and there are eight captures."""
    os.makedirs(cachedir, exist_ok=True)
    arc = lgp.Archive(flevel)
    names = sorted(n for n in arc.names() if arc.is_field(arc.index[n]))
    bad = {}
    for a in range(0, len(names), chunk):
        out = os.path.join(cachedir, 'hay_%d.npz' % a)
        if os.path.exists(out):
            print('   have %d..%d' % (a, min(a + chunk, len(names))),
                  file=sys.stderr)
            continue
        d = {}
        for nm in names[a:a + chunk]:
            try:
                img, drawn = render_big(arc.decompressed(arc.index[nm]), layers)
                ys, xs = np.nonzero(drawn)
                if not ys.size:
                    bad[nm] = 'nothing drawn'
                    continue
                y0, y1 = max(0, ys.min() - 8), min(H, ys.max() + 9)
                x0, x1 = max(0, xs.min() - 8), min(W, xs.max() + 9)
                d[nm] = (pooled_grad(luma(img[y0:y1, x0:x1])).astype(np.float32),
                         int(x0), int(y0))
            except Exception as exc:                            # noqa: BLE001
                bad[nm] = '%s: %s' % (type(exc).__name__, str(exc)[:50])
        np.savez_compressed(out,
                            names=np.array(list(d)),
                            origins=np.array([[v[1], v[2]] for v in d.values()]),
                            **{'p%d' % i: v[0] for i, v in enumerate(d.values())})
        print('   cached %d..%d' % (a, min(a + chunk, len(names))),
              file=sys.stderr)
    return bad


def search(path, hay, top=8, masked=True):
    small, dims = capture_template(path)
    t = pooled_grad(luma(small))
    mask, frac = capture_mask(path)
    res = []
    for nm, plane, (x0, y0) in hay:
        plane = plane.astype(np.float64)
        if masked:
            r, (py, px) = masked_ncc(plane, t, mask)
        else:
            r, (py, px) = ncc_map(plane, t)
        res.append((r, nm, (x0 + px * POOL - CX, y0 + py * POOL - CY)))
    res.sort(reverse=True)
    return res[:top], dims, frac


def main():
    import argparse

    ap = argparse.ArgumentParser(
        description=__doc__.strip().splitlines()[1],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('shots', nargs='*')
    ap.add_argument('--flevel',
                    default=os.environ.get('FLEVEL', 'flevel.lgp'))
    ap.add_argument('--cachedir', default='/tmp/ff7nx_hay')
    ap.add_argument('--build-cache', action='store_true')
    ap.add_argument('--no-mask', action='store_true')
    ap.add_argument('--top', type=int, default=8)
    ap.add_argument('--sanity', action='store_true')
    a = ap.parse_args()

    if a.build_cache or not os.path.isdir(a.cachedir):
        bad = build_cache(a.flevel, a.cachedir)
        print('cache built, %d field(s) unreadable' % len(bad))
    hay = load_cache(a.cachedir)
    print('%d field(s) cached' % len(hay))

    if a.sanity:
        return 0 if sanity(a.flevel, hay) else 1
    for s in a.shots:
        res, dims, frac = search(s, hay, a.top, not a.no_mask)
        print('\n== %s  %dx%d   %.0f%% of the picture masked as pure black'
              % (os.path.basename(s), dims[0], dims[1], 100 * frac))
        for i, (r, nm, (dx, dy)) in enumerate(res, 1):
            print('   %d. %-12s r=%.4f  camera dst (%+d,%+d)'
                  % (i, nm, r, dx, dy))
        if len(res) > 1:
            print('   gap rank1-rank2: %.4f' % (res[0][0] - res[1][0]))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
