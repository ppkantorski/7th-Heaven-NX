#!/usr/bin/env python3
"""
diag_pool.py -- find the allocator `field_load_textures` fails in, and the
constant that sizes its pool.

WHY
===
Wall Market (`mrkt2`, 11 pages / 10.56 MB) shows BLACK SQUARES THAT MOVE WITH
THE CAMERA in build 64. Everything else was eliminated by measurement: no Wall
Market field is over the per-frame tile limit (255/256 against a 426-unit
window that is already an upper bound), none has a tile naming an absent page,
and the `_worst_window` off-by-one is already compensated.

What remains is the failure the build's own log has predicted since the 3x
buffer landed:

    field render targets: 28.12 MB (8 of them), +25.78 MB vs stock
    ! that comes out of the same pool the field background PAGES allocate
      from ... field_load_textures aborts the whole loop on the first page it
      cannot allocate, and every page after it keeps handle 0 and never draws

A dead page draws nothing, so the tiles it owns are black, and WHICH of those
tiles are on screen depends on where the player stands. That is the reported
symptom exactly, and no data defect produces a position-dependent one.

    python3 diag_pool.py [--around 0x9370c8] [--span 0x80]

USAGE NOTES
===========
Addresses are .text offsets, the same convention `ff7nx_heap` and the 512px
patch use (e.g. "loader: depth-2 alloc element size ... .text+0x9370c8").
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

MAIN = os.path.join(_HERE, 'dump', 'exefs', 'main')

# Sizes worth recognising when they turn up as an immediate.
KNOWN = {
    0x02000000: '32 MB',
    0x04000000: '64 MB',
    0x08000000: '128 MB',
    0x0C000000: '192 MB',
    0x10000000: '256 MB',
    0x20000000: '512 MB',
    0x40000000: '1 GB',
    0x00080000: '512 KB (one 512px truecolor page)',
    0x00020000: '128 KB (one 256px truecolor page)',
}


def text_of(path=MAIN):
    import nso_tool
    info = nso_tool.parse_nso(path)
    return info['segments']['.text']['data']


def disasm(buf, off, span, base=0):
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    md.detail = False
    lo = max(0, off - span)
    hi = min(len(buf), off + span)
    out = []
    for ins in md.disasm(bytes(buf[lo:hi]), base + lo):
        out.append((ins.address, ins.mnemonic, ins.op_str))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--around', default='0x9370c8')
    ap.add_argument('--span', default='0x60')
    ap.add_argument('--calls', action='store_true',
                    help='resolve BL targets and show what they lead to')
    a = ap.parse_args(argv)
    off = int(a.around, 0)
    span = int(a.span, 0)

    buf = text_of()
    print('.text is %d bytes (%.1f MB)' % (len(buf), len(buf) / 1048576.0))
    print('\n--- around +0x%X ---' % off)
    for addr, mn, ops in disasm(buf, off, span):
        mark = '  <== ' if abs(addr - off) < 4 else '      '
        note = ''
        if mn in ('movz', 'mov', 'movk') and '#' in ops:
            try:
                v = int(ops.split('#')[-1].split(',')[0], 0)
                if v in KNOWN:
                    note = '   %s' % KNOWN[v]
            except ValueError:
                pass
        print('%s+0x%07X  %-8s %s%s' % (mark, addr, mn, ops, note))

    if a.calls:
        print('\n--- BL targets in that window ---')
        seen = []
        for addr, mn, ops in disasm(buf, off, span):
            if mn == 'bl':
                try:
                    t = int(ops.strip(), 0)
                except ValueError:
                    continue
                if t not in seen:
                    seen.append(t)
        for t in seen:
            print('\n  call -> +0x%07X' % t)
            for addr, mn, ops in disasm(buf, t, 0x30):
                if addr < t:
                    continue
                print('      +0x%07X  %-8s %s' % (addr, mn, ops))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
