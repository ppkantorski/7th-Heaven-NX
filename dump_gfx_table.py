#!/usr/bin/env python3
"""
dump_gfx_table.py -- name the port's native functions.

`exefs/main` carries a name-keyed import table at module offset 0x12C9A70,
0x20 bytes per entry, `entry+0x08` -> name string and `entry+0x10` -> the
real function. Both fields are R_AARCH64_RELATIVE and therefore ZERO in the
file, so a byte search finds nothing; the addends are in `.rela.dyn` and
`nxmap.Main.rel` already exposes them.

This is the cheapest way into any native subsystem in the module. Naming the
whole movie path took one run of this (`fw_movie_update`, `fw_movie_start`,
`gfx_drv_setviewport`); guessing at it from disassembly took hours.

    python3 dump_gfx_table.py dump/exefs/main
    python3 dump_gfx_table.py dump/exefs/main --grep movie
    python3 dump_gfx_table.py dump/exefs/main -o gfx_drv_table.txt
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nxmap                                                 # noqa: E402

TABLE = 0x12C9A70
STRIDE = 0x20
NAME_AT = 0x08
FUNC_AT = 0x10
MAX_ENTRIES = 512


def entries(main_path):
    m = nxmap.Main(main_path)
    img, rel = m.img, m.rel
    out = []
    for i in range(MAX_ENTRIES):
        e = TABLE + i * STRIDE
        name_off = rel.get(e + NAME_AT)
        if name_off is None:
            break
        end = img.find(b'\0', name_off)
        name = img[name_off:end].decode('latin1')
        out.append((i, name, rel.get(e + FUNC_AT)))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().split('\n')[0])
    ap.add_argument('main', help='path to exefs/main')
    ap.add_argument('--grep', help='only names containing this (case '
                                   'insensitive)')
    ap.add_argument('-o', '--out', help='write here instead of stdout')
    args = ap.parse_args(argv)

    rows = entries(args.main)
    if not rows:
        print('no entries at 0x%X -- is this the right module?' % TABLE)
        return 2

    lines = ['# %d entries at module +0x%X, %d bytes each'
             % (len(rows), TABLE, STRIDE),
             '# index  function    name']
    for i, name, fn in rows:
        if args.grep and args.grep.lower() not in name.lower():
            continue
        lines.append('%5d  %-11s %s'
                     % (i, ('0x%X' % fn) if fn else '(null)', name))

    text = '\n'.join(lines)
    if args.out:
        with open(args.out, 'w') as f:
            f.write(text + '\n')
        print('wrote %s (%d entries)' % (args.out, len(rows)))
    else:
        print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
