#!/usr/bin/env python3
"""
measure_dedup.py -- what does the repack cost in PAGES, and how much of that
is duplicate cells?

    python3 measure_dedup.py <flevel.lgp> [--px 256] [--limit N]

The number that breaks is the page count: `field_load_textures` (x86 0x640292)
makes one texture per present page and abandons the loop on the first failure.
So this reports pages, not megabytes.

The art provider here is a STUB that models Cosmos Limit Break's measured
coverage rather than reading the 3 GB .iro:

  * pages below 0x0F are dumped by FFNx as palette 0 only (the engine makes
    ONE texture for them -- field_load_textures sets texheader+0xC = 1 only
    when slot >= 0x0F and depth == 1), so every palette on such a page
    resolves to the SAME image;
  * pages 0x0F and up get one image per palette the tiles actually carry;
  * every cell of the art is opaque.

That last one is optimistic and deliberately so -- it measures the packing,
which is what the change is about, without making the answer depend on which
cells of the mod happen to be transparent.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import lgp                                                       # noqa: E402
import field_bg_native as FN                                     # noqa: E402
import field_bg_repack as RP                                     # noqa: E402

SECTION9 = 8
PAL_BOUNDARY = 0x0F


class StubArt:
    """A PageArt that is opaque everywhere and blits a deterministic pattern."""

    __slots__ = ('px', 'key', 'buf')

    def __init__(self, px, key):
        self.px = px
        self.key = key
        self.buf = None

    def cell_opaque(self, cx, cy, grid):
        return True

    def _pixels(self):
        if self.buf is None:
            import hashlib
            n = self.px * self.px * 2
            seed = hashlib.sha256(repr(self.key).encode()).digest()
            out = bytearray()
            h = seed
            while len(out) < n:
                h = hashlib.sha256(h).digest()
                out += h
            self.buf = bytes(out[:n])
        return self.buf

    def blit_into(self, dst, dst_px, grid, scx, scy, dcx, dcy):
        side = self.px // grid
        src = self._pixels()
        sw = self.px * 2
        dw = dst_px * 2
        s0 = (scy * side) * sw + scx * side * 2
        d0 = (dcy * side) * dw + dcx * side * 2
        n = side * 2
        for y in range(side):
            dst[d0 + y * dw:d0 + y * dw + n] = src[s0 + y * sw:s0 + y * sw + n]


def palettes_on_pages(sec9):
    """{page slot: set(palette)} straight off the tile records."""
    pages, tex_start, tex_end = FN.parse_texture_block(sec9, FN.VANILLA_PX)
    spans = FN._layer_tile_spans(sec9, sec9.find(b'BACK'), tex_start)
    out = defaultdict(set)
    for off in spans:
        out[sec9[off + FN.TILE_TEXTURE_ID]].add(sec9[off + RP.T_PALETTE])
    return pages, out


def present(pages):
    return sum(1 for p in pages if p is not None)


def run(path, px, limit=None, only=None):
    arch = lgp.Archive(path)
    rows = []
    for entry in arch.entries:
        name = entry['name']
        low = name.lower()
        if only and low not in only:
            continue
        if not arch.is_field(entry):
            continue
        try:
            sec = lgp.split_sections(arch.decompressed(entry))[SECTION9]
        except Exception:
            continue
        try:
            pages, pal_on = palettes_on_pages(sec)
        except Exception:
            continue
        before = present(pages)

        cache = {}

        def pals_for(slot, _pal_on=pal_on):
            if slot < PAL_BOUNDARY:
                return {0}
            return set(_pal_on.get(slot, {0}))

        def art_for(slot, pal, _c=cache):
            k = (slot, pal)
            a = _c.get(k)
            if a is None:
                a = _c[k] = StubArt(px, k)
            return a

        try:
            out, st = RP.repack_section9(sec, name, art_for, page_px=px,
                                         pals_for=pals_for)
        except Exception as e:
            rows.append((name, before, before, 0, 'ERR %s' % e))
            continue
        try:
            after_pages, _s, _e = FN.parse_texture_block(out, px)
            after = present(after_pages)
        except Exception as e:
            rows.append((name, before, before, 0, 'ERR-parse %s' % e))
            continue
        rows.append((name, before, after, st.cells, ''))
        if limit and len(rows) >= limit:
            break
    return rows


def report(rows, label):
    ok = [r for r in rows if not r[4]]
    if not ok:
        print('%s: nothing measured' % label)
        return
    grew = [r for r in ok if r[2] > r[1]]
    worst = max(ok, key=lambda r: r[2])
    print('%-22s fields %4d   pages: max %2d (%s)  mean %.2f   '
          'vanilla mean %.2f   grew %d   cells %d'
          % (label, len(ok), worst[2], worst[0],
             sum(r[2] for r in ok) / len(ok),
             sum(r[1] for r in ok) / len(ok),
             len(grew), sum(r[3] for r in ok)))
    over = sorted((r for r in ok if r[2] > 12), key=lambda r: -r[2])[:8]
    if over:
        print('    over 12: ' + ', '.join('%s %d->%d' % (r[0], r[1], r[2])
                                          for r in over))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('flevel')
    ap.add_argument('--px', type=int, default=256)
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--fields', default=None)
    a = ap.parse_args(argv)
    only = set(x.strip().lower() for x in a.fields.split(',')) if a.fields else None
    rows = run(a.flevel, a.px, a.limit, only)
    report(rows, 'px=%d' % a.px)
    errs = [r for r in rows if r[4]]
    if errs:
        print('  %d error(s): %s' % (len(errs), errs[:3]))
    return rows


if __name__ == '__main__':
    main()


def palettes_all(sec9):
    """{page slot: set(palette)} straight off the tile records."""
    _pages, out = palettes_on_pages(sec9)
    return out
