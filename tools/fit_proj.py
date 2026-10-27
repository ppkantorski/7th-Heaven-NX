#!/usr/bin/env python3
"""
tools/fit_proj.py -- turn one probe screenshot into ff7nx_fbresample's fit.

BUILD 327. The projective remap needs the battle camera's ground-plane
homography, and that is per-battle-location data rather than a constant in
the executable -- so it has to be MEASURED, and measured again whenever the
camera it was measured on is not the camera you care about. This is that
measurement, as one command, so it never has to be done by hand again.

    # the fit, from a `texel` probe screenshot
    SEVENTH_NX_FB_PROBE=texel <build, cast Kujata, screenshot>
    python3 tools/fit_proj.py shot.png                 # print the fit
    python3 tools/fit_proj.py shot.png --write         # ... and install it

    # the check, from a `projsrc` probe screenshot
    SEVENTH_NX_FB_PROBE=projsrc <build, cast Kujata, screenshot>
    python3 tools/fit_proj.py shot.png --check

WHAT THE TWO PROBES CARRY
-------------------------
    texel     B = u, G = v, R = 0x40      which texel is drawn at this pixel
    projsrc   B = col>>4, G = row>>4,     which SOURCE that texel sampled
              R = 0x40

Both are drawn through the field's own colour modulation, which is recovered
from the R marker (0x40 nominal) rather than assumed -- getting that wrong is
what makes a fit look plausible and land wrong.

No third-party modules are required: the PNG reader and the solver are both
in here, because this has to run on the machine that runs the build.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import argparse
import os
import re
import struct
import sys
import zlib

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE = nxpaths.src_file('ff7nx_fbresample.py')

FRAME_W, FRAME_H = None, None      # taken from the image
MARKER = 0x40


# --------------------------------------------------------------------------
# a PNG reader, so the tool has no dependencies
# --------------------------------------------------------------------------
def read_png(path):
    """(width, height, rows) for an 8-bit non-interlaced RGB/RGBA PNG.

    Pillow is used when it is installed -- it also reads JPEG, and a
    console's own screenshot is not always a PNG -- and this is the fallback.
    """
    try:
        from PIL import Image                                  # noqa: PLC0415
        im = Image.open(path).convert('RGB')
        w, h = im.size
        data = im.tobytes()
        return w, h, [data[y * w * 3:(y + 1) * w * 3] for y in range(h)]
    except ImportError:
        pass
    raw = open(path, 'rb').read()
    if raw[:8] != b'\x89PNG\r\n\x1a\n':
        sys.exit('%s is not a PNG, and Pillow is not installed to read it '
                 '(pip install pillow)' % path)
    pos, idat, hdr = 8, [], None
    while pos < len(raw):
        ln, kind = struct.unpack_from('>I4s', raw, pos)
        body = raw[pos + 8:pos + 8 + ln]
        if kind == b'IHDR':
            hdr = struct.unpack('>IIBBBBB', body)
        elif kind == b'IDAT':
            idat.append(body)
        elif kind == b'IEND':
            break
        pos += 12 + ln
    w, h, depth, colour, comp, filt, inter = hdr
    if depth != 8 or inter != 0 or colour not in (2, 6):
        sys.exit('only 8-bit non-interlaced RGB/RGBA PNGs, got depth %d '
                 'colour %d interlace %d' % (depth, colour, inter))
    nch = 3 if colour == 2 else 4
    data = zlib.decompress(b''.join(idat))
    stride = w * nch
    out, prev = [], bytearray(stride)
    p = 0
    for _ in range(h):
        ft = data[p]
        line = bytearray(data[p + 1:p + 1 + stride])
        p += 1 + stride
        for x in range(stride):
            a = line[x - nch] if x >= nch else 0
            b = prev[x]
            c = prev[x - nch] if x >= nch else 0
            if ft == 1:
                line[x] = (line[x] + a) & 0xFF
            elif ft == 2:
                line[x] = (line[x] + b) & 0xFF
            elif ft == 3:
                line[x] = (line[x] + ((a + b) >> 1)) & 0xFF
            elif ft == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 0xFF
        prev = line
        if nch == 3:
            out.append(bytes(line))
        else:
            out.append(bytes(b for n in range(w)
                             for b in line[n * 4:n * 4 + 3]))
    return w, h, out


# --------------------------------------------------------------------------
# the solver: an inhomogeneous DLT by normal equations, with trimming
# --------------------------------------------------------------------------
def solve(rows, n):
    """Gaussian elimination with partial pivoting on an n x (n+1) system."""
    m = [list(r) for r in rows]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[piv][c]) < 1e-18:
            raise ZeroDivisionError('singular normal equations')
        m[c], m[piv] = m[piv], m[c]
        d = m[c][c]
        m[c] = [v / d for v in m[c]]
        for r in range(n):
            if r != c and m[r][c]:
                f = m[r][c]
                m[r] = [a - f * b for a, b in zip(m[r], m[c])]
    return [m[r][n] for r in range(n)]


def _norm(pts, a, b):
    """The similarity that centres a point set and scales it to about 1.

    Not optional. Without it the normal equations below are conditioned by
    the square of the coordinate range -- 256 UV units against a unit screen
    -- and the solve comes back with a homography that fits nothing. That is
    exactly what the first draft of this tool did.
    """
    n = float(len(pts))
    mx = sum(p[a] for p in pts) / n
    my = sum(p[b] for p in pts) / n
    d = (sum(abs(p[a] - mx) + abs(p[b] - my) for p in pts) / (2 * n)) or 1.0
    s = 1.0 / d
    return (s, -s * mx, -s * my)


def _raw_homography(pts):
    """[(x, y, u, v)] -> 9 coefficients, h22 = 1, by normal equations."""
    A = [[0.0] * 9 for _ in range(8)]
    for x, y, u, v in pts:
        for co, tgt in (((x, y, 1.0, 0.0, 0.0, 0.0, -u * x, -u * y), u),
                        ((0.0, 0.0, 0.0, x, y, 1.0, -v * x, -v * y), v)):
            for r in range(8):
                cr = co[r]
                if not cr:
                    continue
                row = A[r]
                for c in range(8):
                    row[c] += cr * co[c]
                row[8] += cr * tgt
    return solve(A, 8) + [1.0]


def homography(pts):
    """The same, normalised on both sides and mapped back."""
    sp, tpx, tpy = _norm(pts, 0, 1)
    sq, tqu, tqv = _norm(pts, 2, 3)
    h = _raw_homography([(sp * x + tpx, sp * y + tpy,
                          sq * u + tqu, sq * v + tqv) for x, y, u, v in pts])
    # H = inv(T2) . h . T1, written out rather than inverted numerically
    T1 = [[sp, 0.0, tpx], [0.0, sp, tpy], [0.0, 0.0, 1.0]]
    T2i = [[1.0 / sq, 0.0, -tqu / sq], [0.0, 1.0 / sq, -tqv / sq],
           [0.0, 0.0, 1.0]]
    H = [h[0:3], h[3:6], h[6:9]]
    M = [[sum(T2i[r][t] * sum(H[t][k] * T1[k][c] for k in range(3))
              for t in range(3)) for c in range(3)] for r in range(3)]
    k = M[2][2]
    return [v / k for row in M for v in row]


def apply_h(h, x, y):
    d = h[6] * x + h[7] * y + h[8]
    return ((h[0] * x + h[1] * y + h[2]) / d,
            (h[3] * x + h[4] * y + h[5]) / d)


def _residuals(h, pts):
    out = []
    for x, y, u, v in pts:
        pu, pv = apply_h(h, x, y)
        out.append(((pu - u) ** 2 + (pv - v) ** 2) ** 0.5)
    return out


def fit(pts, keep_fractions=(1.0, 0.9, 0.85, 0.85, 0.9, 0.9)):
    """Least squares, then trim, repeatedly.

    The trim is a FRACTION rather than a threshold: the first solve is done
    over everything the marker matched, sky and HUD edges included, and an
    absolute threshold on that pass throws away the wrong half.
    """
    keep = list(pts)
    h = homography(keep)
    for frac in keep_fractions[1:]:
        res = _residuals(h, keep)
        cut = sorted(res)[max(8, int(frac * len(res)) - 1)]
        keep = [p for p, r in zip(keep, res) if r <= cut]
        h = homography(keep)
    res = sorted(_residuals(h, keep))
    return h, len(keep), res[len(res) // 2]


# --------------------------------------------------------------------------
# reading a probe screenshot
# --------------------------------------------------------------------------
def probe_pixels(path, step=1, top=0.28, skip_hud=True):
    """[(x, y, B, G)] for every pixel carrying the probe's R marker, plus the
    colour modulation recovered from that marker."""
    w, h, rows = read_png(path)
    out, msum, mn = [], 0, 0
    y0 = int(top * h)
    for y in range(y0, h, step):
        row = rows[y]
        hud = skip_hud and y >= 0.67 * h
        for x in range(0, w, step):
            r, g, b = row[3 * x], row[3 * x + 1], row[3 * x + 2]
            if not 0.78 * MARKER <= r <= 0.92 * MARKER:
                continue
            if hud and 0.12 * w <= x <= 0.89 * w:
                continue                       # the battle menu sits here
            msum += r
            mn += 1
            out.append((x / float(w), y / float(h), float(b), float(g)))
    if not mn:
        sys.exit('no probe pixels in %s -- is SEVENTH_NX_FB_PROBE set, and '
                 'is the field up in this frame?' % path)
    return w, h, out, (msum / float(mn)) / MARKER


def do_fit(args):
    w, h, px, s = probe_pixels(args.shot, step=args.step)
    pts = [(x, y, b / s, g / s) for x, y, b, g in px]
    hm, n, med = fit(pts)
    print('%s  %dx%d' % (args.shot, w, h))
    print('  probe pixels        %d' % len(pts))
    print('  colour modulation   %.5f  (R = %.2f against a nominal %d)'
          % (s, s * MARKER, MARKER))
    print('  inliers             %d' % n)
    print('  median residual     %.3f UV units' % med)
    print('  uv at screen centre %.2f, %.2f' % apply_h(hm, 0.5, 0.5))
    text = ('FIT_HUV = (%.10g, %.10g, %.10g,      # -> u\n'
            '           %.10g, %.10g, %.10g,     # -> v\n'
            '           %.10g, %.10g, %.1f)          # denominator'
            % tuple(hm))
    print()
    print(text)
    if med > 3.0:
        print('\n  WARNING: a median over ~3 UV units is not a homography.'
              '\n  Either the disc was rippling hard in this frame, or the'
              '\n  probe is not the one this tool expects.')
    if args.write:
        src = open(MODULE).read()
        new = re.sub(r'FIT_HUV = \([^)]*\)[^\n]*', text, src, count=1)
        if new == src:
            sys.exit('could not find FIT_HUV in %s' % MODULE)
        open(MODULE, 'w').write(new)
        print('\nwritten to %s' % MODULE)
        print('the dial this shot was taken at must be FIT_DISC_PCT '
              '(currently %s)' % re.search(r'FIT_DISC_PCT = ([\d.]+)',
                                           new).group(1))


def do_check(args):
    """A `projsrc` screenshot answers the whole chain at once.

    Every texel reports the source it sampled, in 16-surface-pixel units. If
    the remap is right, the value at screen (x, y) is that screen pixel's own
    position in surface coordinates -- so the fit is a straight line through
    the origin with slope 1, and any error is a number rather than an opinion.
    """
    sys.path.insert(0, _HERE)
    import ff7nx_fbresample as R                                # noqa: PLC0415
    sw, sh = R.surface()
    w, h, px, s = probe_pixels(args.shot, step=args.step)
    n = 0
    sx = sy = sxx = syy = sxy_x = sxy_y = 0.0
    worst = (0.0, None)
    for x, y, b, g in px:
        col, row = (b / s) * 16.0, (g / s) * 16.0
        want_c, want_r = x * sw, y * sh
        ex, ey = col - want_c, row - want_r
        n += 1
        sx += ex
        sy += ey
        sxx += ex * ex
        syy += ey * ey
        sxy_x += want_c
        sxy_y += want_r
        d = (ex * ex + ey * ey) ** 0.5
        if d > worst[0]:
            worst = (d, (x * w, y * h, col, row, want_c, want_r))
    mean = (sx / n, sy / n)
    rms = ((sxx / n) ** 0.5, (syy / n) ** 0.5)
    print('%s  %dx%d, %d probe pixels, surface %dx%d' % (args.shot, w, h, n,
                                                         sw, sh))
    print('  mean error   col %+8.1f   row %+8.1f   surface px'
          % (mean[0], mean[1]))
    print('  rms  error   col %8.1f   row %8.1f   surface px' % rms)
    print('  worst        %.0f surface px at screen (%.0f, %.0f): sampled '
          '(%.0f, %.0f), wanted (%.0f, %.0f)'
          % ((worst[0],) + worst[1]))
    tol = 0.02 * sw
    if abs(mean[0]) < tol and abs(mean[1]) < tol and max(rms) < 2 * tol:
        print('\n  the remap is drawing the frame where the frame was: 1:1 '
              'within %.0f surface px (%.1f screen px).' % (tol, tol * w / sw))
    else:
        print('\n  NOT 1:1. The mean is the registration error and the rms is'
              '\n  the scale error; both are in surface pixels, so divide by'
              '\n  %.1f for screen pixels. Re-fit from a `texel` shot of THIS'
              '\n  camera rather than nudging the coefficients.'
              % (sw / float(w)))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('shot')
    ap.add_argument('--write', action='store_true',
                    help='install the fit into ff7nx_fbresample.py')
    ap.add_argument('--check', action='store_true',
                    help='the shot is a `projsrc` probe; verify the remap')
    ap.add_argument('--step', type=int, default=1,
                    help='sample every Nth pixel (default 1)')
    args = ap.parse_args()
    (do_check if args.check else do_fit)(args)


if __name__ == '__main__':
    main()
