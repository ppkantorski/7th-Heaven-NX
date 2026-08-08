#!/usr/bin/env python3
"""
What does a background-section mod actually change, field by field?

Written for Cosmos Limit Break, where "did this do anything?" is a fair
question: the resolution and the palettes are untouched by design, so the
only way to know which screens are worth looking at in-game is to compare
the sections against the Switch's own flevel.lgp.

Each field is put in one of five buckets:

  identical                 the mod's section is the vanilla section. Nothing
                            can change on screen.
  layout only               the same texture pages, byte for byte; only the
                            tile table differs. Tiles moved, were re-palettised
                            or were added from art already present.
  art changed               same number of pages, different pixels in them.
                            Re-authored or re-quantised tiles, at the same
                            256x256 8-bit the format has always used.
  wider                     pages added, existing pages untouched. The
                            widescreen extension, nothing else.
  wider + art changed       both.

Run from your project folder:

    python3 cosmos_report.py                       # top 40 by pages added
    python3 cosmos_report.py --all                 # every field
    python3 cosmos_report.py --bucket "art changed"
    python3 cosmos_report.py --cache cache/SomeOtherMod

Pages are 256x256 at 1 byte per pixel, so 64 KB each -- that is the number
the field background cap in the GUI is measured in.
"""
import argparse
import glob
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build                                                    # noqa: E402
import lgp                                                      # noqa: E402

PAGE_BYTES = 256 * 256


def texture_pages(sec9):
    """The raw pixels of each present page, or None if this is not a
    background section. Same walk as build._bg_texture_pages, and
    self-validating the same way: 42 slots must consume the section."""
    start = sec9.find(b'TEXTURE')
    if start < 0:
        return None
    o = start + 7
    n = len(sec9)
    out = []
    for _ in range(42):
        if o + 2 > n:
            return None
        present = struct.unpack('<H', sec9[o:o + 2])[0]
        o += 2
        if not present:
            continue
        if o + 4 > n:
            return None
        _size, depth = struct.unpack('<HH', sec9[o:o + 4])
        o += 4
        if depth not in (1, 2):
            return None
        out.append(sec9[o:o + PAGE_BYTES * depth])
        o += PAGE_BYTES * depth
    return out if 0 <= n - o <= 64 else None


def classify(van_sec, mod_sec):
    if van_sec == mod_sec:
        return 'identical', 0, 0
    pv, pm = texture_pages(van_sec), texture_pages(mod_sec)
    if pv is None or pm is None:
        return 'unparsed', 0, 0
    added = len(pm) - len(pv)
    changed = sum(1 for a, b in zip(pv, pm) if a != b)
    if added > 0:
        return ('wider + art changed' if changed else 'wider'), added, changed
    return ('art changed' if changed else 'layout only'), added, changed


def find_flevel():
    dump = build.find_game_dump(HERE)
    wd = dump.workingdir if dump else os.path.join(HERE, 'workingdir')
    path = os.path.join(wd, 'data', 'field', 'flevel.lgp')
    return path if os.path.exists(path) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cache', default=None,
                    help='mod cache folder (default: the one holding '
                         'flevel.lgp/*.chunk.9 under cache/)')
    ap.add_argument('--flevel', default=None, help='vanilla flevel.lgp')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--bucket', default=None)
    ap.add_argument('--top', type=int, default=40)
    args = ap.parse_args()

    flevel = args.flevel or find_flevel()
    if not flevel:
        sys.exit('no flevel.lgp found -- pass --flevel')

    if args.cache:
        chunks = glob.glob(os.path.join(args.cache, '**', 'flevel.lgp',
                                        '*.chunk.9'), recursive=True)
    else:
        chunks = glob.glob(os.path.join(HERE, 'cache', '**', 'flevel.lgp',
                                        '*.chunk.9'), recursive=True)
    if not chunks:
        sys.exit('no *.chunk.9 files found -- is the mod extracted?')
    print(f'{len(chunks)} background section(s) from '
          f'{os.path.relpath(os.path.dirname(chunks[0]), HERE)}')
    print(f'against {flevel}\n')

    archive = lgp.Archive(flevel)
    rows = []
    missing = []
    for p in sorted(chunks):
        name = os.path.basename(p)[:-len('.chunk.9')]
        entry = archive.index.get(name)
        if entry is None or not archive.is_field(entry):
            missing.append(name)
            continue
        van = lgp.split_sections(archive.decompressed(entry))[8]
        with open(p, 'rb') as f:
            mod = f.read()
        bucket, added, changed = classify(van, mod)
        rows.append((name, bucket, added, changed, len(mod) - len(van)))

    counts = {}
    for _n, b, _a, _c, _d in rows:
        counts[b] = counts.get(b, 0) + 1
    order = ['wider + art changed', 'art changed', 'wider', 'layout only',
             'identical', 'unparsed']
    for b in order:
        if b in counts:
            print(f'{counts[b]:5d}  {b}')
    if missing:
        print(f'{len(missing):5d}  no such field on Switch '
              f'({", ".join(missing[:4])})')

    shown = [r for r in rows if r[1] not in ('identical',)]
    if args.bucket:
        shown = [r for r in rows if r[1] == args.bucket]
    shown.sort(key=lambda r: (-r[2], -r[3]))
    if not args.all:
        shown = shown[:args.top]
    if shown:
        print(f'\n{"field":<12} {"what changed":<22} {"pages":>7} '
              f'{"art":>5}  size delta')
        for name, bucket, added, changed, delta in shown:
            print(f'{name:<12} {bucket:<22} '
                  f'{("+" + str(added)) if added else "":>7} '
                  f'{changed or "":>5}  {delta:+,}')
        print('\n"pages" = 256x256 pages added (64 KB each).  '
              '"art" = existing pages whose pixels differ.')
        print('Screens near the top are where a difference is most likely '
              'to be visible in-game.')


if __name__ == '__main__':
    main()
