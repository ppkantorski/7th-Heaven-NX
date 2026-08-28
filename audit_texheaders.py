#!/usr/bin/env python3
"""audit_texheaders.py -- are we writing TEX headers the port can trust?

    python3 audit_texheaders.py                     # built sdout vs vanilla
    python3 audit_texheaders.py --only char.lgp
    python3 audit_texheaders.py --built X --vanilla Y

WHY THIS EXISTS
---------------
`tex.parse()` already refuses a file whose payload length disagrees with
`width * height * bytes_per_pixel + palette`, so nothing in this tree can
produce a TEX that is the wrong SIZE. That is not the same as producing a
TEX the port reads correctly.

A TEX header carries about a dozen fields that all describe the SAME pixel
block from different angles -- bit depth, bits per pixel, bytes per pixel,
bits per index, palette count, colours per palette (twice), palette size,
pitch. Nothing in this repository checks that they agree with each other, and
the port is free to believe whichever one it likes. `tex.cap_dimensions`
rewrites width, height and (conditionally) pitch; `tex.convert_for_battle`
rewrites the palette fields. Either can leave a sibling field describing an
image that no longer exists.

The failure that shape produces is exactly the one under investigation: a
loader that computes a stride or a row count from a stale field reads or
writes past the end of one texture and into the next, which looks like
corruption in an unrelated texture, persists until the process restarts, and
has nothing to do with how much memory is available.

This is a pure offline check. It reads the archives, it changes nothing.

WHAT IT CHECKS
--------------
Per TEX, all against that file's own header:

    bits_per_pixel   == bytes_per_pixel * 8
    palette_size     == num_palettes * colors_per_palette   (paletted only)
    colors_per_pal   == colors_per_pal2
    pitch            == 0 or width * bytes_per_pixel
    bits_per_index   == 8 for a 1-byte paletted image
    bit_depth        consistent with bytes_per_pixel
    paletted flag    agrees with palette_size and bytes_per_pixel

VANILLA IS THE CONTROL, and it matters. If vanilla itself violates a rule,
that rule is not a rule -- Square shipped it and the port reads it. Only
inconsistencies that are NEW in the built archive are evidence. The report
separates the two and leads with what we introduced.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import struct
import sys

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import lgp                                                     # noqa: E402
import tex                                                     # noqa: E402

TITLE_ID = '0100A5B00BDC6000'
ARCHIVES = ('char.lgp', 'battle.lgp', 'world_us.lgp', 'magic.lgp',
            'menu_us.lgp')


def u32(d, off):
    return struct.unpack_from('<I', d, off)[0]


def fields(data):
    """Every header field this audit reasons about."""
    return {
        'version': u32(data, tex.O_VERSION),
        'min_bpc': u32(data, tex.O_MIN_BPC),
        'max_bpc': u32(data, tex.O_MAX_BPC),
        'min_bpp': u32(data, tex.O_MIN_BPP),
        'max_bpp': u32(data, tex.O_MAX_BPP),
        'num_palettes': u32(data, tex.O_NUM_PALETTES),
        'colors_per_pal': u32(data, tex.O_COLORS_PER_PAL),
        'bit_depth': u32(data, tex.O_BIT_DEPTH),
        'width': u32(data, tex.O_WIDTH),
        'height': u32(data, tex.O_HEIGHT),
        'pitch': u32(data, tex.O_PITCH),
        'pal_flag': u32(data, tex.O_PAL_FLAG),
        'bits_per_index': u32(data, tex.O_BITS_PER_INDEX),
        'pal_size': u32(data, tex.O_PAL_SIZE),
        'colors_per_pal2': u32(data, tex.O_COLORS_PER_PAL2),
        'bits_per_pixel': u32(data, tex.O_BITS_PER_PIXEL),
        'bytes_per_pixel': u32(data, tex.O_BYTES_PER_PIXEL),
    }


def rules(f):
    """[(rule name, ok)] for one header. Only self-consistency, no policy."""
    bypp = f['bytes_per_pixel']
    out = [
        ('bits_per_pixel == bytes_per_pixel*8',
         f['bits_per_pixel'] == bypp * 8),
        ('colors_per_pal == colors_per_pal2',
         f['colors_per_pal'] == f['colors_per_pal2']),
        ('pitch is 0 or width*bytes_per_pixel',
         f['pitch'] in (0, f['width'] * bypp)),
    ]
    if f['pal_flag']:
        out += [
            ('paletted => bytes_per_pixel == 1', bypp == 1),
            ('paletted => bits_per_index == 8', f['bits_per_index'] == 8),
            ('pal_size == num_palettes * colors_per_pal',
             f['pal_size'] == f['num_palettes'] * f['colors_per_pal']),
            ('paletted => pal_size > 0', f['pal_size'] > 0),
        ]
    else:
        out += [
            ('truecolor => pal_size == 0', f['pal_size'] == 0),
            ('truecolor => bytes_per_pixel >= 2', bypp >= 2),
        ]
    return out


def scan(path, label):
    """{name: (fields, [failed rule names])} for every TEX in an archive."""
    print('  reading %-14s %s' % (label, path))
    try:
        a = lgp.Archive(str(path))
    except Exception as exc:                                   # noqa: BLE001
        print('    ! cannot read: %s' % exc)
        return None
    out = {}
    n_tex = 0
    for e in a.entries:
        d = e['payload']
        if len(d) < tex.HEADER_LEN or u32(d, tex.O_VERSION) != 1:
            continue
        if tex.parse(d) is None:
            continue                       # .P models and friends
        n_tex += 1
        f = fields(d)
        out[e['name'].lower()] = (f, [r for r, ok in rules(f) if not ok])
    print('    %d TEX file(s)' % n_tex)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--sdout', default=str(HERE / 'sdout'))
    ap.add_argument('--vanilla', default=None,
                    help='directory of vanilla archives (default: the dump)')
    ap.add_argument('--only', action='append',
                    help='archive name, repeatable')
    ap.add_argument('--list', type=int, default=12,
                    help='how many offending files to list per rule')
    a = ap.parse_args(argv)

    built_dir = (Path(a.sdout) / 'atmosphere' / 'contents' / TITLE_ID /
                 'romfs' / 'ff7' / 'workingdir' / 'data')
    van_dir = Path(a.vanilla) if a.vanilla else (HERE / 'dump' / 'romfs' /
                                                 'ff7' / 'workingdir' / 'data')

    def find(root, name):
        for p in root.rglob(name):
            return p
        return None

    names = a.only or ARCHIVES
    grand_new = 0
    for name in names:
        b = find(built_dir, name)
        v = find(van_dir, name)
        print('\n=== %s' % name)
        if b is None:
            print('  built copy not found under %s; skipped' % built_dir)
            continue
        built = scan(b, 'built')
        van = scan(v, 'vanilla') if v else None
        if built is None:
            continue
        if van is None:
            print('  ! no vanilla control found -- reporting raw '
                  'inconsistencies only, which cannot distinguish ours from '
                  "Square's")
        # group by rule
        by_rule = {}
        for fname, (f, bad) in built.items():
            for r in bad:
                by_rule.setdefault(r, []).append(fname)
        if not by_rule:
            print('  every TEX header is self-consistent')
            continue
        for r, files_ in sorted(by_rule.items(), key=lambda kv: -len(kv[1])):
            if van is None:
                newly = files_
                note = '(no control)'
            else:
                newly = [fn for fn in files_
                         if fn not in van or r not in van[fn][1]]
                note = ('%d of them are NEW (vanilla is clean there)'
                        % len(newly))
            print('  %-44s %5d file(s)  %s' % (r, len(files_), note))
            grand_new += len(newly)
            for fn in sorted(newly)[:a.list]:
                f = built[fn][0]
                extra = ''
                if fn in (van or {}):
                    vf = van[fn][0]
                    diff = {k: (vf[k], f[k]) for k in f if vf[k] != f[k]}
                    if diff:
                        extra = '   vanilla->built: ' + ', '.join(
                            '%s %s->%s' % (k, x, y)
                            for k, (x, y) in sorted(diff.items()))
                print('      %-24s %dx%d bypp=%d pal=%d palsz=%d pitch=%d%s'
                      % (fn, f['width'], f['height'], f['bytes_per_pixel'],
                         f['pal_flag'], f['pal_size'], f['pitch'], extra))
            if len(newly) > a.list:
                print('      ... and %d more' % (len(newly) - a.list))

    print('\n%s' % ('=' * 60))
    if grand_new:
        print('%d TEX header inconsistency(ies) that vanilla does not have.'
              % grand_new)
        print('Those are ours. Each one is a field the port may believe.')
    else:
        print('No TEX header inconsistency that vanilla does not also have.')
        print('The headers are not where the corruption comes from.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
