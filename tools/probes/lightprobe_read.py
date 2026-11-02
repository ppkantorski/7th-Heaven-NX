#!/usr/bin/env python3
"""
lightprobe_read.py -- decode the crash report of lightprobe's second L3
click ('LTP1'). See lightprobe.py.

    python3 tools/probes/lightprobe_read.py <crash_report.log>

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
import struct
import sys

sys.path.insert(0, _nx_os.path.dirname(_nx_os.path.abspath(__file__)))
import frameprobe_read as FR                 # noqa: E402

MAGIC = 0x3150544C
MAGIC2 = 0x3250544C
COUNTERS = ('magic', 'frames armed', 'light setups (0x69C3D7)',
            'draw binds tried (0x68E92D)', '  bound', '  no record for the light',
            '  record not fresh', 'relights (0x64CA65/0x64C382)', '  own part record',
            '  no part record', '  part record of another light',
            'vertex lit: own record', 'vertex lit: NOT its own (last setup / previous part)',
            'vertex lit: stock path', 'matrix fix: ours | stock<<16',
            'parts not tracked (table full)')
PATHS = ('vtx-own', 'vtx-LAST', 'vtx-stock', 'mv-own', 'mv-LAST', 'mv-stock')


def report(text, out=sys.stdout):
    w = lambda s='': out.write(s + '\n')
    thread = FR._crashed_thread(text)
    regs = FR.parse_registers(thread)
    stack = FR.parse_dump(thread, 'Stack Dump')
    tls = FR.parse_dump(thread, 'TLS Dump')
    x = [regs.get(i, 0) for i in range(8)]
    c = []
    for v in x:
        c += [v & 0xFFFFFFFF, v >> 32]
    if c[0] not in (MAGIC, MAGIC2):
        w('not a lightprobe report (x0 is 0x%X, not LTP1/LTP2)' % x[0])
        return 1
    fp, lr = regs.get(29, 0), regs.get(30, 0)
    for i in range(1, 16):
        w('%-46s %d' % (COUNTERS[i], c[i]) if i != 14 else
          '%-46s %d | %d' % (COUNTERS[i], c[i] & 0xFFFF, c[i] >> 16))
    if c[0] == MAGIC2:
        w('lights: L0=0x%08X L1=0x%08X   traced poly set 0x%08X, %d relights of it'
          % (fp & 0xFFFFFFFF, fp >> 32, lr & 0xFFFFFFFF, lr >> 32))
        w()
        w('frm site path  out(r g b)    ambient x128   colour sum x64  V.r  bone#  col#')
        blob = tls[:256] + stack[:256]
        for i in range(32):
            v = blob[16 * i:16 * i + 16]
            if not any(v):
                continue
            site = {1: '64CA65', 2: '64C382'}.get(v[0] & 7, '?%d' % (v[0] & 7))
            if v[0] & 8:
                site += '!nopart'
            w('%3d %-6s %-5s %3d %3d %3d   %3d %3d %3d    %3d %3d %3d     %3d  %04X   %04X'
              % (v[7], site, PATHS[v[0] >> 4] if v[0] >> 4 < 6 else '-',
                 v[1], v[2], v[3], v[4], v[5], v[6], v[8], v[9], v[10], v[11],
                 v[12] | v[13] << 8, v[14] | v[15] << 8))
        return 0
    lights = [fp & 0xFFFFFFFF, fp >> 32, lr & 0xFFFFFFFF, lr >> 32]
    w('lights: %s' % ' '.join('L%d=0x%08X' % (i, v) for i, v in enumerate(lights)))
    w()
    w('%-10s %-3s %5s %6s %5s  %5s %5s %4s  %s'
      % ('poly set', 'lt', 'hit', 'nopart', 'mism', 'big', 'small', 'maxd', 'paths'))
    blob = tls[:252] + stack[:252]
    for i in range(42):
        ps, a, b = struct.unpack_from('<3I', blob, 12 * i)
        if not ps:
            continue
        bits = a >> 24
        w('0x%08X L%d %5d %6d %5d  %5d %5d %4d  %s'
          % (ps, bits >> 6, a & 0xFF, (a >> 8) & 0xFF, (a >> 16) & 0xFF,
             b & 0xFF, (b >> 8) & 0xFF, (b >> 16) & 0xFF,
             ' '.join(p for k, p in enumerate(PATHS) if bits >> k & 1)))
    return 0


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    return report(open(argv[1], encoding='utf-8', errors='replace').read())


if __name__ == '__main__':
    sys.exit(main(sys.argv))
