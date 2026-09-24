#!/usr/bin/env python3
"""
frameprobe_read.py -- decode an Atmosphere crash report written by the
SEVENTH_NX_FRAME_PROBE build (see ff7nx_frameprobe.py).

    python3 frameprobe_read.py <crash_report.log> [--flevel <flevel.lgp>]

Reads the crashed thread's registers (X0..X28, FP, LR), its Stack Dump
(the worst frames 0..7) and TLS Dump (worst frames 8..15), and prints:

  * the field, how many frames the visit ran, per-section averages
  * how many frames ran over 16.9 / 18 / 20 / 25 / 33.4 ms
  * the last 52 frame periods, oldest first
  * the worst 16 frames with their full breakdown, worst first
"""
import argparse
import os
import re
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import ff7nx_frameprobe as P                                   # noqa: E402

TICK_MS = 1000.0 / P.CPS
UNIT_MS = TICK_MS * (1 << P.UNIT_SHIFT)
HEX_BYTE = re.compile(r'\b([0-9A-Fa-f]{2})\b')


def _crashed_thread(text):
    """The crashed thread's section only (reports list every thread)."""
    m = re.search(r'Crashed Thread Info:(.*?)(?:\n\S|\Z)', text, re.S)
    return m.group(1) if m else text


def parse_registers(text):
    regs = {}
    for m in re.finditer(r'X\[(\d{1,2})\]\s*:\s*(?:0x)?([0-9A-Fa-f]{1,16})',
                         text):
        regs.setdefault(int(m.group(1)), int(m.group(2), 16))
    for name, idx in (('FP', 29), ('LR', 30)):
        m = re.search(r'\b%s\s*:\s*(?:0x)?([0-9A-Fa-f]{1,16})' % name, text)
        if m:
            regs.setdefault(idx, int(m.group(1), 16))
    return regs


def parse_dump(text, title):
    """Bytes of a 'Stack Dump' / 'TLS Dump' block, in order.

    Atmosphere 1.7 prints each row as `<address> xx xx ... xx` (no colon);
    older layouts use `<address>: xx ...`. Both are accepted.
    """
    lines = text.splitlines()
    out = bytearray()
    for i, line in enumerate(lines):
        if line.strip().startswith(title):
            for row in lines[i + 1:]:
                m = re.match(r'\s+([0-9A-Fa-f]{6,16}):?((?:\s+[0-9A-Fa-f]{2}){1,16})'
                             r'\s*(?:\|.*)?$', row)
                if not m:
                    break
                out += bytes(int(b, 16) for b in m.group(2).split())
            break
    return bytes(out)


def records(blob, fid=None):
    """Worst-frame records. creport may start the dump a few words before
    SP, so both 16-byte phases are tried and the one whose records carry the
    report's own field id wins."""
    best = []
    for phase in (0, 16):
        got = []
        for i in range(phase, len(blob) - 31, 32):
            h = struct.unpack_from('<16H', blob, i)
            if h[0] and (fid is None or h[15] == fid):
                got.append(h)
        if len(got) > len(best):
            best = got
    return best


def load_maplist(path):
    if not path or not os.path.exists(path):
        return None
    import ff7nx_daynight
    return ff7nx_daynight.read_maplist(path)


def name_of(maplist, fid):
    if maplist and 0 <= fid < len(maplist):
        return '%s (%d)' % (maplist[fid], fid)
    return 'field %d' % fid


V2_NAMES = ('logic', 'anim', 'draw', 'limit', 'catchup', 'drvflip',
            'timer', 'texload', 'ogg', 'tcb')
V1_NAMES = ('logic', 'anim', 'draw', 'limit', 'catchup', 'drvflip',
            'trophy', 'texload', 'ogg', 'shader')


def _u64s(blob, n):
    blob = blob.ljust(8 * n, b'\0')
    return list(struct.unpack_from('<%dQ' % n, blob, 0))


def report(text, maplist=None, out=sys.stdout):
    thread = _crashed_thread(text)
    r = parse_registers(thread)
    if len(r) < 31:
        raise SystemExit('only %d registers found in the report' % len(r))
    if r[0] & 0xFFFFFFFF != P.MAGIC:
        raise SystemExit('X0 is %016X, not the probe magic -- this crash was '
                         'not the probe dump' % r[0])
    version = r[0] >> 32
    frames = r[1] & 0xFFFFFFFF
    fid = (r[1] >> 32) & 0xFFFF
    head = (r[1] >> 48) & 0xFFFF
    stack = parse_dump(thread, 'Stack Dump')
    tls = parse_dump(thread, 'TLS Dump')
    w = out.write
    if version == 1:
        # v1: the kernel zeroed x8..x28 at svcBreak, so only the first six
        # sums (x2..x7) and the newest eight periods (FP, LR) survived.
        names = V1_NAMES
        sums = [r[2 + k] for k in range(6)] + [None] * 4
        sump = bk = tot = None
        ring = struct.pack('<QQ', r[29], r[30])
        periods = [p for p in struct.unpack('<8H', ring)]
        top = records(stack, fid) + records(tls[:256], fid)
    else:
        names = P.SECTION_NAMES if version >= 3 else V2_NAMES
        blk = tls[:128]
        q = _u64s(blk, 16)
        sums, sump = q[:10], q[10]
        words = struct.unpack_from('<10I', blk.ljust(128, b'\0'), 88)
        bk, tv = list(words[:5]), words[5:]
        tot = {'textures created': tv[0], 'palette writes': tv[1],
               'ogg opens': tv[2], 'timer callbacks': tv[3],
               'texture Kpixels': tv[4]}
        ring = b''.join(struct.pack('<Q', r[i]) for i in (2, 3, 4, 5, 6, 7,
                                                           29, 30))
        periods = list(struct.unpack('<%dH' % P.PRING_N, ring))
        periods = periods[head:] + periods[:head]
        top = records(stack, fid) + records(tls[128:256], fid)

    w('FIELD  %s   frames this visit: %d   (probe v%d)\n\n'
      % (name_of(maplist, fid), frames, version))
    n = max(frames, 1)
    w('average per frame (ms), whole visit:\n')
    if sump is not None:
        w('  period  %6.2f\n' % (sump * TICK_MS / n))
    for k, nm in enumerate(names):
        if version >= 3 and k in (P.EL_SLOT, P.DEBT_SLOT, P.DRIFT_SLOT):
            continue                     # per-frame snapshots, not times
        if sums[k] is not None:
            w('  %-7s %6.2f\n' % (nm, sums[k] * TICK_MS / n))
    if bk is not None:
        w('\nframes over: ' + '  '.join('%.1fms:%d' % (ms, c) for ms, c in
                                         zip(P.BUCKETS_MS, bk)) + '\n')
        w('totals: ' + ', '.join('%s %d' % kv for kv in tot.items()) + '\n')
    w('\nlast %d periods (ms, oldest first):\n  ' % len(periods))
    w(' '.join('%.1f' % (p * UNIT_MS) for p in periods) + '\n\n')

    top.sort(key=lambda h: -h[0])
    cols = ['period'] + list(names)
    cnt2 = {1: 'shd', 2: 'tcb'}.get(version, 'flg')
    w('worst frames (ms):\n')
    w('  %5s ' % 'frame' + ' '.join('%7s' % c for c in cols)
      + '   tex  pal  ogg  %s  Kpx\n' % cnt2)
    for h in top:
        vals = [h[0] * UNIT_MS] + [v * UNIT_MS for v in h[1:11]]
        hi = h[12] >> 8
        if version >= 3:
            # el / debt / drift carry signs in the flags byte
            for slot, bit in ((P.EL_SLOT, 1), (P.DEBT_SLOT, 2),
                              (P.DRIFT_SLOT, 3)):
                if hi & (1 << bit):
                    vals[1 + slot] = -vals[1 + slot]
            flag = '%s%d' % ('D' if hi & 1 else '-', hi >> 4)
        else:
            flag = '%4d' % hi
        w('  %5d ' % h[14] + ' '.join('%7.2f' % v for v in vals)
          + '  %4d %4d %4d %4s %5d\n' % (h[11] & 0xFF, h[11] >> 8,
                                         h[12] & 0xFF, flag, h[13]))
    if version >= 3:
        w('\n  el@lim = virtual clock - frame baseline at the last limiter '
          'call; debt = debt amount there; drift = hardware counter minus '
          'virtual clock, change over the frame. flg: D = debt flag set, '
          'then the number of limiter calls.\n')
    if not top:
        w('  (no worst-frame records found in the Stack/TLS dumps -- send '
          'the whole report file)\n')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('report')
    ap.add_argument('--flevel', default=os.path.join(
        _HERE, 'dump', 'romfs', 'ff7', 'workingdir', 'data', 'field',
        'flevel.lgp'))
    a = ap.parse_args()
    with open(a.report, 'r', errors='replace') as handle:
        text = handle.read()
    report(text, load_maplist(a.flevel))


if __name__ == '__main__':
    main()
