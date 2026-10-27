#!/usr/bin/env python3
"""
drawprobe_read.py -- decode an Atmosphere crash report written by the
SEVENTH_NX_FRAME_PROBE=draw build (see ff7nx_drawprobe.py).

    python3 drawprobe_read.py <crash_report.log> [--main <exefs/main>]

Prints:
  * how many frames ran, how many had the battle menu, how many had any
    driver draw AFTER the menu
  * the newest 16 menu patterns (a pattern = the exact sequence of driver
    calls inside the battle menu, with pass, blit-pending flag and the
    current depth/colour targets): how often, first and last frame
  * the full events of the most common and of the rarest pattern: the 8
    driver calls right before the menu and the calls inside it

--main maps native return addresses to x86 functions (any main of this
port works: the recompiled layout is the same). Without it they print raw.

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
import bisect
import os
import struct
import sys

_HERE = nxpaths.ROOT
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import ff7nx_drawprobe as D                                    # noqa: E402
from frameprobe_read import (_crashed_thread, parse_dump,      # noqa: E402
                             parse_registers)

NATIVE = {
    0x10D8310: 'field_78', 0x10D9000: 'field_84', 0x10D90B0: 'begin_scene',
    0x10D9270: 'end_scene', 0x10DA880: 'flip', 0x10DA6D0: 'draw_deferred',
    0x10DB1B0: 'draw2d_flat', 0x10DB3D0: 'draw2d_tex',
    0x10DB4D0: 'draw2d_pal', 0x10DB870: 'draw3d_flat', 0x10DBA60: 'lines',
    0x10D68D0: 'clear', 0x10D68C0: 'clear_all', 0x10DF010: 'blit',
    0x10E0E90: 'tex_blit', 0x10DF6E0: 'set_pass', 0x10DCD90: 'core_draw',
    0x10DEB00: 'end_scene_capture', 0x10DECA0: 'end_scene_10deca0',
    0xA1A0: 'guest_indirect_call',
}


class Mapper:
    def __init__(self, main):
        self.m = None
        if main and os.path.exists(main):
            import nxmap
            self.m = nxmap.Main(main)
            inv = sorted((v, k) for k, v in self.m.x86_to_arm.items())
            self.arm = [a for a, _ in inv]
            self.inv = inv
        self.nat = sorted(NATIVE)

    def native(self, off):
        i = bisect.bisect_right(self.nat, off) - 1
        if i >= 0 and off - self.nat[i] < 0x400:
            return '+0x%X (%s+0x%X)' % (off, NATIVE[self.nat[i]],
                                       off - self.nat[i])
        if self.m is not None and off < 0x10C0000:
            i = bisect.bisect_right(self.arm, off) - 1
            if i >= 0:
                arm, x86 = self.inv[i]
                lo, hi = self.m.extent(x86)
                if lo <= off < hi:
                    return '+0x%X (x86 fn 0x%X)' % (off, x86)
        i = bisect.bisect_right(self.nat, off) - 1
        if i >= 0 and off - self.nat[i] < 0x1000:
            return '+0x%X (%s+0x%X)' % (off, NATIVE[self.nat[i]],
                                       off - self.nat[i])
        return '+0x%X' % off

    def guest(self, va):
        if not va:
            return '-'
        if self.m is not None and 0x401000 <= va < 0x1000000:
            c = self.m.containing(va)
            if c:
                return '0x%X (x86 fn 0x%X)' % (va, c[0])
        return '0x%X' % va


def ev(e):
    w = e & 0xFFFFFFFF
    return {'lr': (w & 0x7FFFFF) << 2, 'id': (w >> 23) & 15,
            'pass': (w >> 27) & 15, 'pending': w >> 31,
            'depth': (e >> 32) & 0xFFFF, 'colour': e >> 48}


def decode(text):
    t = _crashed_thread(text)
    regs = parse_registers(t)
    if regs.get(0, 0) & 0xFFFFFFFF != D.MAGIC:
        raise SystemExit('not a draw-probe report (x0 = %#x)' % regs.get(0, 0))
    ver = regs[0] >> 32
    if ver != D.VERSION:
        raise SystemExit('draw probe v%d report; this reader is v%d'
                         % (ver, D.VERSION))
    stack = parse_dump(t, 'Stack Dump').ljust(256, b'\0')
    tls = parse_dump(t, 'TLS Dump').ljust(256, b'\0')
    x1, x2, x3 = regs.get(1, 0), regs.get(2, 0), regs.get(3, 0)
    out = {'version': ver, 'frames': x1 & 0xFFFFFFFF, 'menufr': x1 >> 32,
           'fw2': x2 & 0xFFFFFFFF, 'over': x2 >> 32,
           'nsig': x3 & 0xFFFF, 'imax': (x3 >> 16) & 0xFF,
           'imin': (x3 >> 24) & 0xFF}
    out['newest'] = [regs.get(r, 0) for r in (4, 5, 6, 7, 29, 30)]
    start = max(0, out['nsig'] - D.DUMP_META)
    pats = []
    for k in range(D.DUMP_META):
        sig, cnt, first, last, nev, c0, c1, c2 = struct.unpack_from(
            '<IIHHBBBB', stack, 16 * k)
        if start + k >= out['nsig']:
            break
        pats.append({'idx': start + k, 'sig': sig, 'count': cnt,
                     'first': first, 'last': last, 'nev': nev,
                     'c0': c0, 'c1': c1, 'c2': c2})
    out['patterns'] = pats
    out['det_max'] = list(struct.unpack_from('<16Q', tls, 0))
    out['det_min'] = list(struct.unpack_from('<16Q', tls, 128))
    return out


def _events(mp, det, nev=None, p=print):
    before, inside = det[:8], det[8:]
    p('    before the menu (oldest first):')
    for e in before:
        if e:
            _line(mp, e, p)
    p('    inside the menu%s:' % ('' if nev is None or nev <= 8 else
                                   ' (first 8 of %d)' % nev))
    for e in inside:
        if e:
            _line(mp, e, p)


def _line(mp, e, p=print):
    d = ev(e)
    p('      %-13s pass %-2d %s depth ..%04X colour ..%04X  %s'
      % (D.NAMES.get(d['id'], '?%d' % d['id']), d['pass'],
         'PENDING' if d['pending'] else '       ', d['depth'], d['colour'],
         mp.native(d['lr'])))


def report(out, mp):
    p = print
    p('draw probe v%d: %d frames, battle menu ran on %d; frames with a '
      'draw after the menu: %d' % (out['version'], out['frames'],
                                    out['menufr'], out['fw2']))
    p('%d distinct menu patterns (%d frames past the table)'
      % (out['nsig'], out['over']))
    first = ev(out['det_max'][8])['id'] if out['det_max'][8] else None
    p('depth clear right before the menu: %s'
      % ('yes (the 686 placement)' if first == 8 else
         'no (stock, or 692+, which clears before 0x6CE8B3 instead)'))
    p('')
    p('newest %d patterns: idx count first last  draws before/inside/after'
      '  events-in-menu' % len(out['patterns']))
    for q in out['patterns']:
        tag = ''
        if q['idx'] == out['imax']:
            tag += '  <- most common'
        if q['idx'] == out['imin']:
            tag += '  <- rarest'
        p('  %3d %6d %5d %5d   %3d/%3d/%3d   %3d   sig %08X%s'
          % (q['idx'], q['count'], q['first'], q['last'], q['c0'], q['c1'],
             q['c2'], q['nev'], q['sig'], tag))
    p('')
    p('most common pattern (#%d), its last frame:' % out['imax'])
    _events(mp, out['det_max'])
    p('')
    p('rarest pattern (#%d), its last frame:' % out['imin'])
    _events(mp, out['det_min'])
    p('')
    p('newest pattern (#%d), inside the menu:' % (max(out['nsig'], 1) - 1))
    for e in out['newest']:
        if e:
            _line(mp, e, p)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('report')
    ap.add_argument('--main', default=os.path.join(_HERE, 'main_stock'))
    args = ap.parse_args(argv)
    with open(args.report, errors='replace') as handle:
        text = handle.read()
    report(decode(text), Mapper(args.main))


if __name__ == '__main__':
    main()
