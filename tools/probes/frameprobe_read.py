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
import os
import re
import struct
import sys

_HERE = nxpaths.ROOT
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
               ('GL calls' if version >= 4 else 'texture Kpixels'): tv[4]}
        ring = b''.join(struct.pack('<Q', r[i]) for i in (2, 3, 4, 5, 6, 7,
                                                           29, 30))
        periods = list(struct.unpack('<%dH' % P.PRING_N, ring))
        periods = periods[head:] + periods[:head]
        top = records(stack, fid) + records(tls[128:256], fid)

    if version >= 5:
        top = []                         # the dump carries the GL profile
    w('FIELD  %s   frames this visit: %d   (probe v%d)\n\n'
      % (name_of(maplist, fid), frames, version))
    n = max(frames, 1)
    w('average per frame (ms), whole visit:\n')
    if sump is not None:
        w('  period  %6.2f\n' % (sump * TICK_MS / n))
    if version >= 4:
        names = P.SECTION_NAMES
        snaps = (P.DEBT_SLOT,)
    elif version == 3:
        names = ('logic', 'anim', 'draw', 'limit', 'catchup', 'drvflip',
                 'el@lim', 'texload', 'debt', 'drift')
        snaps = (P.EL_SLOT, P.DEBT_SLOT, P.DRIFT_SLOT)
    else:
        snaps = ()
    for k, nm in enumerate(names):
        if k in snaps:
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
      + '   tex  pal  ogg  %s  %s\n' % (cnt2, 'glN' if version >= 4 else 'Kpx'))
    for h in top:
        vals = [h[0] * UNIT_MS] + [v * UNIT_MS for v in h[1:11]]
        hi = h[12] >> 8
        if version >= 3:
            # el / debt / drift carry signs in the flags byte (v4: debt)
            for slot, bit in (((P.DEBT_SLOT, 2),) if version >= 4 else
                              ((P.EL_SLOT, 1), (P.DEBT_SLOT, 2),
                               (P.DRIFT_SLOT, 3))):
                if hi & (1 << bit):
                    vals[1 + slot] = -vals[1 + slot]
            flag = '%s%d' % ('D' if hi & 1 else '-', hi >> 4)
        else:
            flag = '%4d' % hi
        w('  %5d ' % h[14] + ' '.join('%7.2f' % v for v in vals)
          + '  %4d %4d %4d %4s %5d\n' % (h[11] & 0xFF, h[11] >> 8,
                                         h[12] & 0xFF, flag, h[13]))
    if version >= 6:
        _drv_profile(stack, tls, sums, n, w, version)
        return
    if version >= 5:
        _profile(stack, tls, w)
        return
    if version >= 4:
        w('\n  gl = time inside the GL driver (all calls but the four '
          'texture/blit uploads), swap = eglSwapBuffers alone; both are PART '
          'of draw/drvflip/texload, not added to them. glN = GL calls. '
          'debt = limiter debt; flg: D = debt flag, then limiter calls.\n')
        _movable(top, w)
    elif version >= 3:
        w('\n  el@lim = virtual clock - frame baseline at the last limiter '
          'call; debt = debt amount there; drift = hardware counter minus '
          'virtual clock, change over the frame. flg: D = debt flag set, '
          'then the number of limiter calls.\n')
    if not top:
        w('  (no worst-frame records found in the Stack/TLS dumps -- send '
          'the whole report file)\n')


def profile_rows(stack, tls):
    """v5: [(name, ms per frame, calls per frame, repeat %)].

    creport may start the stack dump a few bytes before SP; the frame end
    keeps SP 16-aligned and the dump starts on a 16-byte row, so the table is
    at the start of the Stack Dump as the probe wrote it."""
    names = P.profile_names()
    raw = stack[:256].ljust(256, b'\0') + tls[128:160].ljust(32, b'\0')
    pct = tls[160:160 + P.PROF_MAX].ljust(P.PROF_MAX, b'\0')
    rows = []
    for i, name in enumerate(names):
        t, n = struct.unpack_from('<HH', raw, 4 * i)
        rows.append((name, t * UNIT_MS, n, pct[i]))
    return rows


def _profile(stack, tls, w):
    rows = profile_rows(stack, tls)
    total = sum(r[1] for r in rows)
    calls = sum(r[2] for r in rows)
    w('\nGL profile, per frame (whole visit): %.2f ms in %d calls\n'
      % (total, calls))
    w('  %-30s %8s %7s %8s %7s\n' % ('function', 'ms', 'calls', 'us/call',
                                     'repeat'))
    for name, ms, n, pct in sorted(rows, key=lambda r: -r[1]):
        if n == 0 and ms == 0:
            continue
        w('  %-30s %8.3f %7d %8.2f %6d%%\n'
          % (name, ms, n, 1000.0 * ms / n if n else 0.0, pct))
    w('\n  repeat = calls whose first (up to four) integer arguments equal '
      'the previous call to the same function: what a state filter could '
      'drop. A lower bound for glEnable/glDisable (repeats interleave), an '
      'over-count for binds that differ per texture unit, and meaningless '
      'for uploads and draws.\n')


def drv_rows(stack, tls, version=P.DRV_VERSION):
    """v6: [(name, ms per frame, calls per frame, GL ms inside per frame)]."""
    raw = stack[:256].ljust(256, b'\0')
    gl = tls[128:256].ljust(128, b'\0')
    rows = []
    for i, name in enumerate(P.drv_names(version)):
        t, n = struct.unpack_from('<HH', raw, 4 * i)
        g = struct.unpack_from('<H', gl, 2 * i)[0]
        rows.append((name, t * UNIT_MS, n, g * UNIT_MS))
    return rows


def _drv_profile(stack, tls, sums, frames, w, version):
    rows = drv_rows(stack, tls, version)
    if version == P.LAYER_VERSION:
        _layer_profile(rows, w)
        return
    if version >= 9:
        _gpu_profile(rows, sums, frames, w, version)
        return
    first = P.game_first()
    game = rows[first:]
    nsub = len(P.DRV_SUBCALLS)
    subs = rows[first - nsub:first]
    rows = rows[:first - nsub]
    top = rows[-1]
    w('\nnative gfx driver, per frame (whole visit). ms includes everything '
      'the function calls; gl is the part of it inside GL; own = ms - gl.\n')
    w('  %-30s %8s %7s %8s %8s %8s\n' % ('function', 'ms', 'calls', 'gl',
                                         'own', 'us/call'))
    for name, ms, n, gl in sorted(rows[:-1], key=lambda r: -r[1]):
        if n == 0 and ms == 0:
            continue
        w('  %-30s %8.3f %7d %8.3f %8.3f %8.2f\n'
          % (name, ms, n, gl, ms - gl, 1000.0 * ms / n if n else 0.0))
    name, ms, n, gl = top
    w('  %-30s %8.3f %7d %8.3f %8.3f\n' % (name, ms, n, gl, ms - gl))
    if sums[P.DRAW] is not None:
        draw = sums[P.DRAW] * TICK_MS / frames
        glall = sums[P.GL] * TICK_MS / frames
        w('\n  outermost driver calls: %.2f ms, %.2f of it GL. All GL this '
          'frame: %.2f ms (draw holds %.2f ms in total). The rest of draw, '
          'outside both, is the translated game (and the port code it calls '
          'directly). Nested rows overlap their callers; only the last row '
          'adds up. draw_smoothlines is counted as draw_flatlines.\n'
          % (ms, gl, glall, draw))
    if any(r[2] or r[1] for r in subs):
        w('\nfield_78\'s calls into game code (stock sites only):\n')
        for name, ms, n, gl in subs:
            w('  %-30s %8.3f %7d %8.3f %8.3f %8.2f\n'
              % (name, ms, n, gl, ms - gl, 1000.0 * ms / n if n else 0.0))
    if any(r[2] or r[1] for r in game):
        # v6e: the GL slot of these rows holds the outermost driver time
        w('\nthe translated game\'s calls, per frame, by caller. drv is the '
          'native driver time inside (its GL included); game = ms - drv.\n')
        groups = P.game_rows(version)
        for g, (_span, label, _c, _o) in enumerate(P.game_groups(version)):
            mine = [r for r, (_n, k) in zip(game, groups) if k == g]
            w('\n  %s calls:\n' % label)
            w('  %-30s %8s %7s %8s %8s %8s\n' % ('callee', 'ms', 'calls',
                                                 'drv', 'game', 'us/call'))
            tms = tdrv = 0.0
            for name, ms, n, drv in sorted(mine, key=lambda r: -r[1]):
                tms += ms
                tdrv += drv
                if n == 0 and ms == 0:
                    continue
                w('  %-30s %8.3f %7d %8.3f %8.3f %8.2f\n'
                  % (name, ms, n, drv, ms - drv,
                     1000.0 * ms / n if n else 0.0))
            w('  %-30s %8.3f %7s %8.3f %8.3f\n' % ('(sum)', tms, '', tdrv,
                                                   tms - tdrv))
            if g == 0 and sums[P.DRAW] is not None:
                w('  draw_everything\'s own code (DRAW outside these calls):'
                  ' %.2f ms\n' % (sums[P.DRAW] * TICK_MS / frames - tms))
        w('\n  A caller\'s row includes its callees\' rows below it. calls '
          'is a per-frame average, rounded down (0 = fewer than one a '
          'frame).\n')


def _layer_profile(rows, w):
    """v12: the foreground layers' boxes, per frame (whole visit)."""
    first = P.game_first()
    lr = rows[first:first + len(P.LAYER_ROWS)]
    t = [r[1] / UNIT_MS for r in lr]           # the raw dumped u16s
    n = [r[2] for r in lr]
    merges = n[0]
    w('\nforeground layers, per frame (whole visit). Each merge runs about '
      'four full-screen passes; the box is what the layer actually drew '
      '(TL vertex x, y in the game\'s 640x480 space).\n')
    w('  merges %d (box unknown: %d, nothing drawn: %d); draws into the '
      'layer: %d TL, %d 3D\n' % (merges, n[1], n[2], n[11], n[12]))
    boxed = merges - n[1] - n[2]
    if boxed > 0:
        area = t[0] * 64.0 / boxed
        ext = [t[k] * 16.0 / boxed - 1024 for k in (3, 4, 5, 6)]
        w('  mean box: x %.0f..%.0f, y %.0f..%.0f, %.0f px = %.1f%% of '
          '640x480\n' % (ext[0], ext[1], ext[2], ext[3], area,
                          100.0 * area / P.LAYER_REF_AREA))
    w('  merges by box size: <25%%: %d, 25-50%%: %d, 50-75%%: %d, '
      '>=75%%: %d\n' % (n[7], n[8], n[9], n[10]))
    w('  (per-frame counts are rounded down; a mean is over the boxed '
      'merges)\n')


def _gpu_profile(rows, sums, frames, w, version=P.GPU_VERSION):
    """v9: the GPU wait after each outermost driver call (the GPU time of
    what it submitted) and the waits outside them, per frame. v11: and the
    GPU time of each GPU_SPLIT pass on its own, at any depth."""
    first = P.game_first()
    nsub = len(P.DRV_SUBCALLS)
    funcs = rows[:first - nsub - 1]
    gpu = rows[len(P.drv_names()):]
    split = [r for r in rows[first:len(P.drv_names())] if r[0]] \
        if version >= 11 else []
    w('\nGPU time, per frame (whole visit). gpu = the wait for the GPU right '
      'after an outermost call of the function returned, with the GPU idle '
      'when it started: the GPU time of what that call submitted. ms is the '
      'CPU time of the call (GPU waits excluded).\n')
    w('  %-30s %8s %7s %8s %8s\n' % ('function', 'gpu', 'calls', 'ms',
                                     'gpu/call'))
    total = 0.0
    for name, ms, n, g in sorted(funcs, key=lambda r: -r[3]):
        total += g
        if n == 0 and ms == 0 and g == 0:
            continue
        w('  %-30s %8.3f %7d %8.3f %8.3f\n'
          % (name, g, n, ms, g / n if n else 0.0))
    w('  %-30s %8.3f\n' % ('(driver calls, sum)', total))
    if split:
        w('\nGPU time of single passes (v11), wherever they are called: a '
          'pass inside a driver call waits for the GPU before it starts (that '
          'wait is the "inside callers" row below) and after it ends, so its '
          'gpu is its own work and its caller\'s row above no longer holds '
          'it. A pass called outside the driver counts as an outermost call '
          '(its time is also in the "(all, outermost)" row).\n')
        w('  %-30s %8s %7s %8s %8s\n' % ('pass', 'gpu', 'calls', 'ms',
                                         'gpu/call'))
        for name, ms, n, g in sorted(split, key=lambda r: -r[3]):
            w('  %-30s %8.3f %7d %8.3f %8.3f\n'
              % (name, g, n, ms, g / n if n else 0.0))
        for name, _ms, _n, g in gpu:
            if name == P.GPU_ROWS[5]:
                w('  %-30s %8.3f\n' % (name, g))
    inside = None
    if split:
        # v11: top holds every wait inside an outermost call (the passes'
        # and the "inside callers" row's too); the functions' rows hold
        # what is left after their passes
        total = rows[first - nsub - 1][3]
        inside = P.GPU_ROWS[5]
        w('  %-30s %8.3f\n' % ('(all inside driver calls)', total))
    w('\nGPU waits outside the driver calls (what other code queued):\n')
    for name, _ms, _n, g in gpu:
        if name == inside:
            continue
        total += g
        w('  %-30s %8.3f\n' % (name, g))
    w('  %-30s %8.3f\n' % ('(all GPU, sum)', total))
    w('\n  The CPU waits for the GPU at every one of these points, so this '
      'build\'s frame times are meaningless; only the gpu column is the '
      'measurement. A wait can read 0 where the bound framebuffer cannot be '
      'read back as RGBA bytes (the read is refused without waiting).\n')


def _movable(top, w):
    """v4: what a render thread could take off the game's core, for the
    worst frames: the GL driver time (gl + swap), against the frame's work
    (period minus the limiter's idle spin)."""
    rows = []
    for h in top:
        period = h[0] * UNIT_MS
        work = period - h[1 + P.LIMIT] * UNIT_MS
        gl = (h[1 + P.GL] + h[1 + P.SWAP]) * UNIT_MS
        if work > 0:
            rows.append((period, work, gl))
    if not rows:
        return
    n = len(rows)
    work = sum(r[1] for r in rows) / n
    gl = sum(r[2] for r in rows) / n
    w('\n  worst %d frames: %.2f ms of work each, %.2f ms of it inside the '
      'GL driver (%.0f%%). That share is the ceiling on what moving GL '
      'submission to another core could save; the rest is the game itself, '
      'which cannot leave its core.\n' % (n, work, gl, 100.0 * gl / work))


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
