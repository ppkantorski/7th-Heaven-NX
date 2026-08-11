#!/usr/bin/env python3
"""
diag_addedtiles.py -- the tiles the widening ADDED, in screen order, with the
mod's art brightness and alpha coverage at the atlas slot each one names.

Settles whether the black margin cells are

  (A) slots for which the mod ships no usable art -- opaque near-black, alpha
      255, nothing to dilate from inside the block; the fix has to come from
      the SCREEN neighbours, or
  (B) a stride/offset error -- the art exists but at a different atlas slot.

(B) requires spare bright slots that no tile claims. This prints the claimed /
unclaimed split so that can be checked rather than assumed.

    python3 diag_addedtiles.py md8_1
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import struct
import sys
import tempfile

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import diag_common as DC              # noqa: E402
import ff7nx_marginart as MA          # noqa: E402
import field_bg_repack                # noqa: E402
import iro                            # noqa: E402
import lgp                            # noqa: E402

TILE = 16
UV_SCALE = 10_000_000
T_DSTX, T_DSTY, T_PAL, T_TEX, T_TEX2 = 2, 4, 22, 32, 34
T_SRC_X_BIG = 42
CHUNK_DIR = 'cache/CosmosLimitBreak/LIMIT BREAK/flevel.lgp'


def build_provider(px):
    with open(os.path.join(_HERE, 'settings.json')) as fh:
        settings = json.load(fh)
    path = os.path.join(_HERE, 'mods', 'CosmosLimitBreak.iro')
    xml = iro.read_one(path, 'mod.xml')
    manifest = None
    if xml:
        with tempfile.NamedTemporaryFile('wb', suffix='.xml',
                                         delete=False) as tf:
            tf.write(xml)
            tmp = tf.name
        manifest = iro.Manifest(tmp)
        os.unlink(tmp)
    opts = settings.get('CosmosLimitBreak.iro', {}).get('options', {})
    folders = iro.active_folders(manifest, opts) if manifest else []
    allowed = {f.lower().replace('\\', '/').rstrip('/') + '/'
               for f in folders} or None
    return field_bg_repack.ArtProvider([(path, allowed)], px, print)


def tiles_of(sec9, layer_want=1):
    pages, tex_start, _e, _px = DC.parse_pages(sec9)
    pmap = {p.slot: p for p in pages if p is not None}
    out = []
    for layer, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), tex_start):
        if layer != layer_want:
            continue
        for o in offs:
            dx = struct.unpack_from('<h', sec9, o + T_DSTX)[0]
            dy = struct.unpack_from('<h', sec9, o + T_DSTY)[0]
            slot = sec9[o + T_TEX]
            fx = sec9[o + T_TEX2]
            eff = fx if (fx and fx in pmap) else slot
            p = pmap.get(eff)
            grid = 8 if (p is not None and p.size_flag) else 16
            step = 256 // grid
            u, v = struct.unpack_from('<II', sec9, o + T_SRC_X_BIG)
            sx = int(round(u / UV_SCALE * grid)) * step
            sy = int(round(v / UV_SCALE * grid)) * step
            out.append((dx, dy, sec9[o + T_PAL], eff, sx, sy))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('field')
    ap.add_argument('--px', type=int, default=512)
    ap.add_argument('--gate', type=float, default=24.0,
                    help="ff7nx_marginart's EMPTY SOURCE gate")
    ap.add_argument('--vanilla', default='game_data_files/field/flevel.lgp')
    a = ap.parse_args(argv)

    VA = lgp.Archive(os.path.join(_HERE, a.vanilla))
    vsec9 = lgp.split_sections(VA.decompressed(VA.index[a.field]))[8]
    vt = tiles_of(vsec9)
    vkey = {(d[0], d[1]) for d in vt}

    with open(os.path.join(_HERE, CHUNK_DIR,
                           f'{a.field}.chunk.9'), 'rb') as fh:
        wt = tiles_of(fh.read())

    prov = build_provider(a.px)
    art = MA.provider_source(prov)
    cache = {}

    def block(slot, sx, sy):
        got = cache.get(slot)
        if got is None:
            got = art(a.field, slot, 0) or False
            cache[slot] = got
        if got is False:
            return None, None
        img, _u = got
        s = img.shape[0] // 256
        b = img[sy * s:(sy + TILE) * s, sx * s:(sx + TILE) * s]
        if b.shape[:2] != (TILE * s, TILE * s):
            return None, None
        rgb = np.ascontiguousarray(b[..., :3]).astype(np.float32)
        al = (b[..., 3].astype(np.float32) if b.shape[2] > 3
              else np.full(b.shape[:2], 255.0, np.float32))
        return rgb, al

    # every atlas slot any tile claims, on any page
    claimed = collections.defaultdict(set)
    for d in vt + wt:
        claimed[d[3]].add((d[4], d[5]))

    added = [d for d in wt if (d[0], d[1]) not in vkey]
    print(f'{a.field}: {len(added)} added tiles')
    print(f'{"dx":>6} {"dy":>6} {"slot":>5} {"pal":>4} {"src":>11} '
          f'{"max":>7} {"mean":>7} {"cov%":>6}  verdict')
    dark = []
    for d in sorted(added, key=lambda t: (t[0], t[1])):
        dx, dy, pal, slot, sx, sy = d
        rgb, al = block(slot, sx, sy)
        if rgb is None:
            print(f'{dx:>6} {dy:>6} {slot:>5} {pal:>4} '
                  f'{f"({sx},{sy})":>11}      -       -      -  NO ART')
            continue
        cov = float((al >= 8).mean()) * 100
        mx = float(rgb[al >= 8].max()) if (al >= 8).any() else 0.0
        mn = float(rgb.mean())
        bad = mx <= a.gate
        if bad:
            dark.append((dx, dy))
        print(f'{dx:>6} {dy:>6} {slot:>5} {pal:>4} {f"({sx},{sy})":>11} '
              f'{mx:>7.1f} {mn:>7.2f} {cov:>6.1f}  '
              f'{"EMPTY SOURCE" if bad else ""}')

    print(f'\n  added tiles under the {a.gate:g} gate: {len(dark)} of '
          f'{len(added)}')

    # (B) check: is there spare bright art no tile claims?
    for page in sorted({d[3] for d in added}):
        got = art(a.field, page, 0)
        if got is None:
            continue
        img, _u = got
        s = img.shape[0] // 256
        n = 256 // TILE
        rgb = np.ascontiguousarray(img[..., :3]).astype(np.float32)
        cell = rgb.reshape(n, TILE * s, n, TILE * s, 3).max(axis=(1, 3, 4))
        free_bright = free_tot = 0
        for r in range(n):
            for c in range(n):
                if (c * TILE, r * TILE) in claimed[page]:
                    continue
                free_tot += 1
                if cell[r, c] > a.gate:
                    free_bright += 1
        print(f'  page {page}: unclaimed slots {free_tot}, '
              f'of which bright {free_bright}')

    # screen-neighbour availability for the dark ones
    bright_at = {}
    for d in wt:
        rgb, al = block(d[3], d[4], d[5])
        if rgb is None:
            continue
        mx = float(rgb[al >= 8].max()) if (al >= 8).any() else 0.0
        bright_at[(d[0], d[1])] = mx
    ok = 0
    for dx, dy in dark:
        nb = [bright_at.get((dx + ox, dy + oy), 0.0)
              for ox, oy in ((TILE, 0), (-TILE, 0), (0, TILE), (0, -TILE))]
        if max(nb) > a.gate:
            ok += 1
    print(f'  dark tiles with at least one NON-empty screen neighbour: '
          f'{ok} of {len(dark)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
