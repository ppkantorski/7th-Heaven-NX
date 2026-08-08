#!/usr/bin/env python3
"""
diag_margin.py -- can the camera reach a spot where the 16:9 margin is empty?

    python3 diag_margin.py <flevel.lgp>
    python3 diag_margin.py <flevel.lgp> nmkin_1 nmkin_2 nmkin_3
    python3 diag_margin.py <flevel.lgp> --csv margins.csv --scale 3

WHY THIS AND NOT THE OTHER TWO
==============================
`diag_fieldwidth.py` asks "does the art span enough to fill 16:9 *somewhere*".
That is necessary but not sufficient. A field can have 624 units of art and
still show an empty band, because the CAMERA decides which 427 of them you
are looking at -- and the camera is clamped by the range in section 8, which
was written for a 320-unit view.

This joins the two: the camera range from section 8, the art extent from
section 9, and the widescreen window, and answers the only question that
matters:

    at the WORST camera position this field allows, does the visible window
    run off the end of the art, and by how much?

THE ARITHMETIC
==============
`field_layer1_pick_tiles` draws a tile when `cam - 336 < tile.x < cam`, and
the visible 4:3 window is `[cam - 320, cam]` in tile units. Widescreen
widens the visible window to `640/WS_SCALE/2` units, centred the same way:

    half   = visible / 2
    window = [c - half,  c + half]     centred on the camera point c

The camera itself is clamped. Stock FF7 clamps to `[left + 160, right - 160]`
(`HALF_WIDTH_43`). FFNx -- and this build, where `camera ranges written`
includes a `clamp` count -- rewrites the stored range so the same stock code
lands on `[left + hw, right - hw]` with

    hw = 160 + min(53, (right - left) / 2 - 160)

which pulls the camera FURTHER from the art edge, exactly so the wider
window still fits. Whether a given field got that treatment is visible here:
compare `clamped` against `stock`.

    left  slack = (c_lo - half) - art_x0
    right slack = art_x1 - (c_hi + half)

Negative slack is an empty band, in tile units. Multiply by 2 for game
units, and by `screen_w / visible` for screen pixels.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, '..')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import lgp                                                      # noqa: E402
import ff7nx_wsdata as W                                        # noqa: E402
import diag_common as DC                                        # noqa: E402

SECTION8 = 7                  # zero-based; section 8 is triggers/gateways
SECTION9 = 8
TILE_PX = 16
TILE_DST_X = 2
HALF_WIDTH_43 = 160
HALF_WIDTH_CAP = 53

# Visible game units for each field-buffer preset (640 / WS_SCALE), halved
# into tile units by `mult` = 2.
VISIBLE_TILE_UNITS = {0: 320.0, 1: 428.0, 2: 427.0, 3: 1280.0 * 2 / 3 / 2}


def ffnx_half_width(size):
    return HALF_WIDTH_43 + min(HALF_WIDTH_CAP, size // 2 - HALF_WIDTH_43)


def art_extent(sec9):
    ext = DC.tile_extents(sec9)
    return None if ext is None else ext['x']


def reference_sizes(path):
    """
    {field: section-8 range size} from an UNMODIFIED flevel.

    The only honest way to answer "was the widescreen clamp baked into this
    file?" is to compare against the file it was baked from. See measure().
    """
    arc = lgp.Archive(path)
    out = {}
    for key in sorted(arc.index):
        e = arc.index[key]
        if not arc.is_field(e):
            continue
        try:
            secs = lgp.split_sections(arc.decompressed(e))
            rng = W.read_section8_range(secs[DC.SECTION8])
        except Exception:                                      # noqa: BLE001
            continue
        if rng is not None:
            out[e['name'].lower()] = int(rng['right']) - int(rng['left'])
    return out


def measure(path, only=None, visible=428.0, reference=None):
    """
    `reference` is {field: original range size} from a vanilla flevel. Without
    it, `clamped` is None -- UNKNOWN -- rather than a guess.

    THE OLD TEST WAS DEAD CODE. It read:

        cam_lo  = left + HALF_WIDTH_43
        clamped = abs((cam_lo - left) - want_hw) < 2 or size <= 2 * want_hw

    and `cam_lo - left` is `HALF_WIDTH_43` by construction, so the first term
    is the constant `abs(160 - want_hw) < 2` and the whole flag collapses to
    `size <= 2 * ffnx_half_width(size)`. That is a statement about the
    arithmetic, not about the file: nothing read off disk survives into it.

    It is why nmkin_1 was labelled "[STOCK 4:3 clamp -- not widened]" when it
    had in fact been shrunk 624 -> 516, 54 units a side, exactly like the
    other three fields in that set. The slack figures never depended on the
    flag and were always right.
    """
    arc = lgp.Archive(path)
    rows, skipped = [], []
    half = visible / 2.0
    for key in sorted(arc.index):
        e = arc.index[key]
        if not arc.is_field(e):
            continue
        if only and e['name'].lower() not in only:
            continue
        try:
            raw = arc.decompressed(e)
            secs = lgp.split_sections(raw)
            rng = W.read_section8_range(secs[DC.SECTION8])
            art = art_extent(secs[DC.SECTION9])
        except Exception as exc:                               # noqa: BLE001
            skipped.append((e['name'], str(exc)[:50]))
            continue
        if rng is None or art is None:
            skipped.append((e['name'], 'no range or no tiles'))
            continue
        left, right = int(rng['left']), int(rng['right'])
        size = right - left
        # What the stock code will compute from the range that is in the file
        cam_lo, cam_hi = left + HALF_WIDTH_43, right - HALF_WIDTH_43
        # Was the widescreen clamp baked into this file? Answerable only
        # against the range it was baked FROM, so without a reference this
        # stays None and the report says "unknown" instead of inventing a
        # boolean.
        orig = None if reference is None else reference.get(
            e['name'].lower())
        if orig is None:
            clamped = None
        else:
            # ff7nx_wsbake shrinks the stored range by 2*(want_hw - 160),
            # with want_hw computed from the ORIGINAL size. +-4 units of
            # tolerance for rounding on either end.
            want = 2 * (ffnx_half_width(orig) - HALF_WIDTH_43)
            clamped = want > 0 and abs((orig - size) - want) <= 4
        lslack = (cam_lo - half) - art[0]
        rslack = art[1] - (cam_hi + half)
        rows.append({'name': e['name'], 'left': left, 'right': right,
                     'size': size, 'cam_lo': cam_lo, 'cam_hi': cam_hi,
                     'x0': art[0], 'x1': art[1], 'span': art[1] - art[0],
                     'lslack': lslack, 'rslack': rslack,
                     'worst': min(lslack, rslack), 'clamped': clamped,
                     'orig_size': orig})
    return rows, skipped


def _clamp_note(r):
    if r['clamped'] is None:
        return '   [clamp unknown -- pass --vanilla to decide]'
    if r['clamped']:
        return '   [clamped for widescreen, shrunk %d units]' % (
            r['orig_size'] - r['size'])
    return '   [STOCK 4:3 clamp -- not widened]'


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__.strip().splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('flevel')
    ap.add_argument('fields', nargs='*')
    ap.add_argument('--scale', type=int, default=1, choices=(0, 1, 2, 3))
    ap.add_argument('--screen', type=int, default=1280)
    ap.add_argument('--top', type=int, default=25)
    ap.add_argument('--csv')
    ap.add_argument('--vanilla', metavar='FLEVEL',
                    help='an unmodified flevel.lgp to compare section-8 '
                         'ranges against. Without it the "camera clamp" '
                         'column reads "unknown", because whether the clamp '
                         'was baked in cannot be decided from the baked file '
                         'alone.')
    a = ap.parse_args(argv)

    visible = VISIBLE_TILE_UNITS[a.scale]
    only = {f.lower() for f in a.fields} or None
    reference = None
    if a.vanilla:
        print('reading reference %s ...' % a.vanilla)
        reference = reference_sizes(a.vanilla)
        print('  %d field(s) with a camera range' % len(reference))
    print('reading %s ...' % a.flevel)
    rows, skipped = measure(a.flevel, only, visible, reference)
    if not rows:
        print('! nothing measured')
        for n, why in skipped[:5]:
            print('    %s: %s' % (n, why))
        return 2

    # tile units -> screen px. The whole visible window fills the screen
    # width, so it is screen/visible -- NOT half that. Getting this wrong
    # halved every band width the tool reported.
    px = a.screen / visible

    print()
    print('field buffer %dx: the visible window is %.1f tile units '
          '(4:3 is 320)' % (a.scale, visible))
    print('so widescreen asks for %.1f extra tile units on EACH side'
          % ((visible - 320) / 2.0))
    print('1 tile unit = %.2f screen px at %dp' % (px, a.screen))
    print()

    if only:
        for r in sorted(rows, key=lambda r: r['name']):
            print('%-12s camera range %d .. %d  (size %d)%s'
                  % (r['name'], r['left'], r['right'], r['size'],
                     _clamp_note(r)))
            print('             camera travels %d .. %d'
                  % (r['cam_lo'], r['cam_hi']))
            print('             art          %d .. %d   (span %d)'
                  % (r['x0'], r['x1'], r['span']))
            for side, s in (('left', r['lslack']), ('right', r['rslack'])):
                if s >= 0:
                    print('             %-5s slack %+7.1f units   covered'
                          % (side, s))
                else:
                    print('             %-5s slack %+7.1f units   EMPTY BAND '
                          'up to %.0f screen px' % (side, s, -s * px))
            print()
        return 0

    bad = [r for r in rows if r['worst'] < 0]
    unclamped = [r for r in rows if r['clamped'] is False]
    unknown = [r for r in rows if r['clamped'] is None]
    print('%d field(s) measured' % len(rows))
    print('  %4d can show an EMPTY BAND at some camera position'
          % len(bad))
    if unknown:
        print('  %4d camera clamps UNKNOWN -- pass --vanilla <flevel> to '
              'decide' % len(unknown))
    else:
        print('  %4d still carry the STOCK 4:3 camera clamp' % len(unclamped))
        print('  %4d of those two overlap'
              % len([r for r in bad if r['clamped'] is False]))
    if skipped:
        print('  %4d had no camera range or no tiles' % len(skipped))
    print()
    print('Worst first -- "slack" is how far the art extends PAST the widest')
    print('the camera can look. Negative is the size of the empty band:')
    print()
    print('  %-12s %8s %8s  %7s  %s'
          % ('field', 'left', 'right', 'px', 'camera clamp'))
    for r in sorted(bad, key=lambda r: r['worst'])[:a.top]:
        print('  %-12s %+8.1f %+8.1f  %7.0f  %s'
              % (r['name'], r['lslack'], r['rslack'], -r['worst'] * px,
                 {None: 'unknown', True: 'widened',
                  False: 'STOCK 4:3'}[r['clamped']]))

    if a.csv:
        import csv
        with open(a.csv, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['field', 'cam_left', 'cam_right', 'cam_lo', 'cam_hi',
                        'art_x0', 'art_x1', 'span', 'left_slack',
                        'right_slack', 'worst_px', 'clamped', 'orig_size'])
            for r in sorted(rows, key=lambda r: r['name']):
                w.writerow([r['name'], r['left'], r['right'], r['cam_lo'],
                            r['cam_hi'], r['x0'], r['x1'], r['span'],
                            '%.1f' % r['lslack'], '%.1f' % r['rslack'],
                            '%.0f' % max(0.0, -r['worst'] * px),
                            '' if r['clamped'] is None else int(r['clamped']),
                            '' if r['orig_size'] is None else r['orig_size']])
        print()
        print('wrote %s' % a.csv)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
