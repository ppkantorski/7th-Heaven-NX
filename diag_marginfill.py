#!/usr/bin/env python3
"""
diag_marginfill.py -- HANDOFF-65 SS4. Decide, offline, over all 711 fields,
which layer-1 margin tiles are FLAT FILLER and whether recolouring them is
safe.

WHAT A "MARGIN TILE" IS
=======================
A layer-1 tile whose 16x16 destination rectangle lies WHOLLY outside the 4:3
picture, i.e. dst_x + 16 <= -160 or dst_x >= 160. A tile that straddles the
boundary is never touched: half of it is inside the picture.

WHAT "FLAT FILLER" IS
=====================
Its 16x16 source block on its texture page is a SINGLE value -- one palette
index on a depth-1 page, one packed colour on a depth-2 page.

THE SAFETY TEST -- and it is the whole point of this file
=========================================================
Recolouring is done on the SOURCE, which is shared. A (page, palette, index)
triple that any IN-PICTURE tile also samples must not be rewritten, or art
inside the 4:3 frame changes colour. So every tile in every layer is walked,
its source block read, and the set of values it samples recorded. A margin
filler value is ACCEPTED only if it appears in no in-picture block anywhere.

Reports, never writes.
"""
from __future__ import annotations

import os
import struct
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import numpy as np

import lgp
import diag_common as DC

TILE = 16
HALF_43 = 160                 # the 4:3 picture is dst_x in [-160, 160)
T_DSTX, T_DSTY, T_SRCX, T_SRCY, T_PAL, T_TEX = 2, 4, 10, 12, 22, 32


class Unreadable(ValueError):
    pass


def palette_pages(sec):
    """(npg, cpp, colours[npg][cpp] as u16) or raise. Same discovery rule as
    ff7nx_bgkey.palette_block: a layout that does not close exactly is
    refused."""
    for hdr in (8, 12, 16):
        if len(sec) < hdr:
            continue
        if hdr == 8:
            _x, _y, cpp, npg = struct.unpack_from('<HHHH', sec, 0)
        elif hdr == 12:
            _l, _x, _y, cpp, npg = struct.unpack_from('<IHHHH', sec, 0)
        else:
            _a, _l, _x, _y, cpp, npg = struct.unpack_from('<IIHHHH', sec, 0)
        if 1 <= cpp <= 1024 and 1 <= npg <= 256 and hdr + 2 * cpp * npg == len(sec):
            v = np.frombuffer(sec, '<u2', count=cpp * npg,
                              offset=hdr).reshape(npg, cpp)
            return hdr, npg, cpp, v
    raise Unreadable('palette does not close')


def page_array(p):
    """A page as a 2-D array of its native source unit: u8 index for depth 1,
    u16 packed colour for depth 2. Indexed [y][x] in PAGE pixels."""
    if p.depth == 1:
        return np.frombuffer(p.data, np.uint8).reshape(256, 256), 1
    return np.frombuffer(p.data, '<u2').reshape(p.px, p.px), p.px // 256


def block(arr, k, sx, sy):
    """The 16x16 source block a tile samples, at page scale k."""
    b = arr[sy * k:sy * k + TILE * k, sx * k:sx * k + TILE * k]
    if b.shape != (TILE * k, TILE * k):
        return None
    return b


def scan(raw):
    """
    One field.

    Returns dict with:
      margin   {(slot, pal, value): n_tiles}   flat margin fillers
      inside   {(slot, pal, value)}            every value any in-picture or
                                               boundary-straddling tile samples
      depth    {slot: depth}
      pal      (hdr, npg, cpp, colours)
    """
    parts = lgp.split_sections(raw)
    hdr, npg, cpp, cols = palette_pages(parts[3])
    sec9 = parts[8]
    surv = DC.survey(sec9)
    pages = {p.slot: p for p in surv['pages']}
    cache = {}

    margin = defaultdict(int)
    inside = set()
    depth = {s: p.depth for s, p in pages.items()}
    n_margin_tiles = 0
    pal_used = set()          # palette pages any tile references
    flat_by_depth = defaultdict(int)

    for layer, offs in DC.walk_layers(sec9, surv['back_start'],
                                      surv['tex_start']):
        for o in offs:
            slot = sec9[o + T_TEX]
            p = pages.get(slot)
            if p is None:
                continue
            if slot not in cache:
                cache[slot] = page_array(p)
            arr, k = cache[slot]
            sx, sy = sec9[o + T_SRCX], sec9[o + T_SRCY]
            b = block(arr, k, sx, sy)
            if b is None:
                continue
            pid = sec9[o + T_PAL] if p.depth == 1 else 0
            if p.depth == 1:
                pal_used.add(sec9[o + T_PAL])
            dx = struct.unpack_from('<h', sec9, o + T_DSTX)[0]

            # Only layer 1 can be "the margin". Layers 2/3/4 are overlays and
            # parallax; anything they sample counts as IN-PICTURE, because a
            # value they use must not be recoloured either.
            is_margin = (layer == 1 and
                         (dx + TILE <= -HALF_43 or dx >= HALF_43))
            if is_margin:
                n_margin_tiles += 1
                u = np.unique(b)
                if u.size == 1:
                    margin[(slot, pid, int(u[0]))] += 1
                    flat_by_depth[p.depth] += 1
                else:
                    margin[(slot, pid, None)] += 1      # not flat
            else:
                for v in np.unique(b):
                    inside.add((slot, pid, int(v)))
    return {'margin': dict(margin), 'inside': inside, 'depth': depth,
            'npg': npg, 'cpp': cpp, 'cols': cols, 'hdr': hdr,
            'n_margin_tiles': n_margin_tiles, 'pal_used': pal_used,
            'free_pal': [i for i in range(npg) if i not in pal_used],
            'flat_by_depth': dict(flat_by_depth),
            'page_px': surv['page_px']}


def rgb555(v):
    r = (v & 0x1F) * 255 // 31
    g = ((v >> 5) & 0x1F) * 255 // 31
    b = ((v >> 10) & 0x1F) * 255 // 31
    return '#%02X%02X%02X' % (r, g, b)


def rgb565(v):
    r = ((v >> 11) & 0x1F) * 255 // 31
    g = ((v >> 5) & 0x3F) * 255 // 63
    b = (v & 0x1F) * 255 // 31
    return '#%02X%02X%02X' % (r, g, b)


def classify(info):
    """
    (accepted, refused, notflat) where each is a list of
    (slot, pal, value, n_tiles, reason).

    ACCEPTED  flat filler whose source value no in-picture tile samples
    REFUSED   flat filler that IS sampled inside the picture -- shared art
    NOTFLAT   margin tiles carrying real art
    """
    acc, ref, notflat = [], [], []
    for (slot, pid, val), n in sorted(info['margin'].items(),
                                      key=lambda kv: -kv[1]):
        if val is None:
            notflat.append((slot, pid, None, n, 'real art'))
            continue
        d = info['depth'].get(slot, 1)
        if d == 1 and val == 0:
            # index 0 is already the transparency key; nothing to do and
            # rewriting it is what HANDOFF-62 killed.
            ref.append((slot, pid, val, n, 'index 0 is the colour key'))
            continue
        if (slot, pid, val) in info['inside']:
            ref.append((slot, pid, val, n, 'sampled inside the picture'))
            continue
        acc.append((slot, pid, val, n, 'depth%d' % d))
    return acc, ref, notflat


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('flevel')
    ap.add_argument('--fields', nargs='*', default=None)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--verbose', action='store_true')
    a = ap.parse_args()

    arc = lgp.Archive(a.flevel)
    names = sorted(n for n in arc.names() if arc.is_field(arc.index[n]))
    if a.fields:
        names = [n for n in names if n in a.fields]
    if a.limit:
        names = names[:a.limit]

    tot = dict(fields=0, unreadable=0, no_margin=0, art_margin=0,
               flat=0, accepted=0, refused=0, acc_tiles=0,
               flat_tiles_d1=0, flat_tiles_d2=0, fields_flat_d2=0,
               fields_with_free_pal=0, fields_flat_any=0, npg_max=0)
    bad = []
    per_field = {}
    for n in names:
        try:
            info = scan(arc.decompressed(arc.index[n]))
        except Exception as exc:                                # noqa: BLE001
            tot['unreadable'] += 1
            bad.append((n, '%s: %s' % (type(exc).__name__, str(exc)[:60])))
            continue
        tot['fields'] += 1
        acc, ref, notflat = classify(info)
        per_field[n] = (acc, ref, notflat, info)
        if not info['n_margin_tiles']:
            tot['no_margin'] += 1
            continue
        if notflat:
            tot['art_margin'] += 1
        if acc or ref:
            tot['flat'] += 1
        tot['accepted'] += len(acc)
        tot['refused'] += len(ref)
        tot['acc_tiles'] += sum(x[3] for x in acc)
        fb = info['flat_by_depth']
        tot['flat_tiles_d1'] += fb.get(1, 0)
        tot['flat_tiles_d2'] += fb.get(2, 0)
        tot['fields_flat_d2'] += 1 if fb.get(2, 0) else 0
        tot['fields_flat_any'] += 1 if (fb.get(1, 0) or fb.get(2, 0)) else 0
        tot['fields_with_free_pal'] += 1 if info['free_pal'] else 0
        tot['npg_max'] = max(tot['npg_max'], info['npg'])
        if a.verbose or (a.fields and len(names) <= 12):
            print('\n== %s   pages %s  page_px %d  npg %d' %
                  (n, info['depth'], info['page_px'], info['npg']))
            print('   margin tiles %d' % info['n_margin_tiles'])
            for slot, pid, val, cnt, why in acc:
                d = info['depth'][slot]
                col = (rgb555(int(info['cols'][pid][val])) if d == 1
                       else rgb565(val))
                print('   ACCEPT slot %2d pal %2d val %4d  %s  %5d tiles (%s)'
                      % (slot, pid, val, col, cnt, why))
            for slot, pid, val, cnt, why in ref:
                print('   REFUSE slot %2d pal %2d val %4s  %5d tiles (%s)'
                      % (slot, pid, val, cnt, why))
            for slot, pid, val, cnt, why in notflat:
                print('   ART    slot %2d pal %2d              %5d tiles'
                      % (slot, pid, cnt))

    print('\n---- %d fields' % len(names))
    for k in ('fields', 'unreadable', 'no_margin', 'art_margin', 'flat',
              'accepted', 'refused', 'acc_tiles', 'flat_tiles_d1',
              'flat_tiles_d2', 'fields_flat_d2', 'fields_flat_any',
              'fields_with_free_pal', 'npg_max'):
        print('%-12s %d' % (k, tot[k]))
    if bad:
        print('unreadable:')
        for n, why in bad[:10]:
            print('   ', n, why)


if __name__ == '__main__':
    main()
