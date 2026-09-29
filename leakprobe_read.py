#!/usr/bin/env python3
"""
leakprobe_read.py -- decode the crash report of an R3 click with leakprobe on
(v3 'LEK3'; v2 'LEAK' reports are still read).

    python3 leakprobe_read.py <crash_report.log>
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import frameprobe_read as FR                 # noqa: E402

MAGIC_V2 = 0x4B41454C
MAGIC_V3 = 0x334B454C
MAGIC_V4 = 0x344B454C
MAGIC_V5 = 0x354B454C
MAGIC_V6 = 0x364B454C
MAGIC_V7 = 0x374B454C
P6_FMT = '<II5II8Q8IIIQ'                      # v6: + terr
F6_FMT = '<9I2HhHI'                           # v6: interval..sub, terr, tris, draws, zoom, alt, view, pad
P4_FMT = '<I5I5Q5IIQ'                         # v4: frames, over[5], sum[5], max[5], tris_max, tris_sum
F4_FMT = '<6I2HhH'                            # v4: interval, pre, win, far, post, tris, draws, zoom, alt, view
P5_FMT = '<II5II7Q7IIQ'                       # v5: frames, own, over[5], pad, sum[7], max[7], tris_max, tris_sum
F5_FMT = '<8I2HhH'                            # v5: + proj, sub before tris
assert struct.calcsize(P4_FMT) == 96 and struct.calcsize(F4_FMT) == 32
assert struct.calcsize(P5_FMT) == 128 and struct.calcsize(F5_FMT) == 40
assert struct.calcsize(P6_FMT) == 144 and struct.calcsize(F6_FMT) == 48
NAMES = {0x42: 'fship_1', 0x43: 'fship_12', 0x44: 'fship_2', 0x45: 'fship_22',
         0x46: 'fship_23', 0x47: 'fship_24', 0x48: 'fship_25', 0x49: 'fship_3',
         0x4A: 'fship_4', 0x4B: 'fship_42', 0x4C: 'fship_5'}
MODES = {2: 'field', 3: 'battle', 4: 'world'}
SPARSE = (24, 32, 48, 64, 96, 128, 192, 256)
W_FMT = '<16h8h16hHHHHiiiiiiIIhhI'
assert struct.calcsize(W_FMT) == 128


def visits(blob):
    out = []
    for i in range(0, len(blob) - 31, 32):
        r = struct.unpack_from('<II4H4HIHH', blob, i)
        if not r[0] and not r[1]:
            continue
        out.append(r)
    return out


def vname(key):
    mode, field = key >> 16, key & 0xFFFF
    name = MODES.get(mode, 'mode %d' % mode)
    if mode == 2:
        name += ' ' + NAMES.get(field, str(field))
    return name


def world(blob, k):
    r = struct.unpack_from(W_FMT, blob, 128 * k)
    y_first, y_sparse, y_last = r[0:16], r[16:24], r[24:40]
    ctl_f, ctl_l, hw_f, hw_l = r[40:44]
    x_f, z_f, x_l, z_l, y_max, y_lctl = r[44:50]
    frames, ring, takeoff = r[50], r[51], r[52]
    return dict(y_first=y_first, y_sparse=y_sparse, y_last=y_last,
                ctl_f=ctl_f, ctl_l=ctl_l, hw_f=hw_f, hw_l=hw_l,
                x_f=x_f, z_f=z_f, x_l=x_l, z_l=z_l, y_max=y_max,
                y_lctl=y_lctl, frames=frames, ring=ring, takeoff=takeoff)


def ys(vals, ctl, hw, n=16):
    out = []
    for i in range(n):
        tag = ('' if (ctl >> i) & 1 else '*') + ('' if (hw >> i) & 1 else '^')
        out.append('%d%s' % (vals[i], tag))
    return ' '.join(out)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    th = FR._crashed_thread(open(argv[1], errors='replace').read())
    regs = FR.parse_registers(th)
    x = [regs.get(i, 0) for i in range(31)]
    magic = x[0] >> 32
    if magic not in (MAGIC_V2, MAGIC_V3, MAGIC_V4, MAGIC_V5, MAGIC_V6, MAGIC_V7):
        print('not a leakprobe report (x0 = %016x)' % x[0])
        return 1
    v3 = magic in (MAGIC_V3, MAGIC_V4, MAGIC_V5, MAGIC_V6, MAGIC_V7)
    v4 = magic in (MAGIC_V4, MAGIC_V5, MAGIC_V6, MAGIC_V7)
    v5 = magic in (MAGIC_V5, MAGIC_V6, MAGIC_V7)
    v6 = magic in (MAGIC_V6, MAGIC_V7)
    v7 = magic == MAGIC_V7
    print('leakprobe %s: now in %s; visit %d; %d live textures, %d parked'
          % ('v7' if v7 else 'v6' if v6 else 'v5' if v5 else 'v4' if v4 else 'v3' if v3 else 'v2', vname(x[0] & 0xFFFFFFFF), x[1] >> 32,
             x[2] >> 32, x[2] & 0xFFFFFFFF))
    print('  GL errors since boot: %d (%d out of memory)' % (x[3] >> 32, x[3] & 0xFFFFFFFF))
    stack = FR.parse_dump(th, 'Stack Dump')
    tls = FR.parse_dump(th, 'TLS Dump')
    rs = [] if v7 else visits(stack[:256]) if v3 else visits(stack) + visits(tls)
    print('  %-4s %-16s %7s  %-14s %5s %6s %5s %5s  %s'
          % ('#', 'visit', 'frames', 'tex start>end', 'peak', 'parked', 'err', 'oom', 'heap free MB'))
    for (key, frames, ts, te, tp, parked, nerr, noom, first, last, fend, seq, ef) in rs:
        print('  %-4d %-16s %7d  %5d > %-6d %5d %6d %5d %5d  %.2f'
              % (seq, vname(key), frames, ts, te, tp, parked, nerr, noom, fend / 1048576))
    if not v3:
        return 0
    print()
    hw_on = x[5] >> 56
    print('worldfar altitude state: saved=%s  y=%d  x=%d z=%d  pending=%d lost=%d'
          '  off=%d  restores=%d'
          % ('yes' if hw_on else 'no', struct.unpack('<i', struct.pack('<I', x[5] & 0xFFFFFFFF))[0],
             struct.unpack('<i', struct.pack('<I', x[6] >> 32))[0],
             struct.unpack('<i', struct.pack('<I', x[6] & 0xFFFFFFFF))[0],
             (x[5] >> 32) & 0xFFFF, (x[5] >> 48) & 0xFF, x[7] & 0xFFFFFFFF, x[7] >> 32))
    if not x[30]:
        print('  (no worldfar block in this main)')
    if len(tls) < 256:
        print('  (no TLS dump)')
        return 0
    if v4:
        perf(tls, v5, v6)
    if v7:
        gfx(stack, x[4])
    return 0
    for k, label in ((0, 'the world visit before last'), (1, 'the last world visit')):
        w = world(tls, k)
        if not w['frames']:
            print('%s: none' % label)
            continue
        print('%s: %d frames, take-off height %d, highest y %d, last y with control'
              ' in the Highwind %d' % (label, w['frames'], w['takeoff'], w['y_max'], w['y_lctl']))
        print('  from (%d, %d) to (%d, %d)' % (w['x_f'], w['z_f'], w['x_l'], w['z_l']))
        print('  first 16 y :', ys(w['y_first'], w['ctl_f'], w['hw_f']))
        print('  y at frame :', ' '.join('%d:%d' % (f, y) for f, y in zip(SPARSE, w['y_sparse'])
                                         if f <= w['frames']))
        n = min(w['ring'], 16)
        print('  last %2d y  :' % n, ys(w['y_last'], w['ctl_l'], w['hw_l'], n))
    print('  (* = control off, ^ = not the Highwind view)')
    return 0


def _gl_calls(img, plt, f, depth=2, seen=None):
    """The GL functions a method reaches through direct calls (a few levels)."""
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    seen = set() if seen is None else seen
    if f in seen or depth < 0:
        return []
    seen.add(f)
    out = []
    for i in Cs(CS_ARCH_ARM64, CS_MODE_ARM).disasm(img[f:f + 0x600], f):
        if i.mnemonic == 'bl':
            t = int(i.op_str[1:], 16)
            if t in plt:
                if plt[t] not in out:
                    out.append(plt[t])
            elif 0x1100000 <= t < 0x1150000:
                for n in _gl_calls(img, plt, t, depth - 1, seen):
                    if n not in out:
                        out.append(n)
        if i.mnemonic == 'ret':
            break
    return out


def gfx(stack, x4):
    """v7: the GL device layer's methods by time over the world frames."""
    import ff7nx_audio_cave as AC
    import leakprobe as L
    import nsosyms
    frames = x4 >> 32
    print()
    print('GL device-layer methods over %d world frames (costliest first):' % frames)
    try:
        dump = os.path.join(HERE, 'dump', 'exefs', 'main')
        b = open(dump, 'rb').read()
        segs, raw = AC.segments(b)
        slots = L.gfx_slots(segs, raw)
        m, plt = nsosyms.imports(dump)
    except Exception as exc:                                   # noqa: BLE001
        slots, m, plt = [], None, {}
        print('  (no dump to name them: %s)' % exc)
    print('  %4s %9s %9s %8s  %-10s %s' % ('slot', 'ms/frame', 'us/call', 'calls/fr', 'method', 'GL it reaches'))
    for k in range(32):
        a, us = struct.unpack_from('<II', stack, 8 * k)
        idx, cpf = a & 0xFFFF, a >> 16
        if not us:
            continue
        up = {row: name for row, name in L.UPLOADS.values()}
        if idx in up:                                          # v7c: bytes, not time
            total = us * 96.0 / 5.0
            per_call = total / (cpf * frames) if cpf and frames else 0
            print('  %4d %8.0f KB/frame %7.0f B/call %5d/fr  %s (glBufferData size)'
                  % (idx, total / 1024.0 / frames if frames else 0, per_call, cpf, up[idx]))
            continue
        per_frame = us / 1000.0 / frames if frames else 0
        per_call = (us / (cpf * frames)) if cpf and frames else 0
        name, gl = '?', ''
        if idx < len(slots):
            _e, slot, target = slots[idx]
            name = '+0x%X' % target
            if m is not None:
                gl = ', '.join(_gl_calls(m.img, plt, target)[:6])
        print('  %4d %9.3f %9.1f %8d  %-10s %s' % (idx, per_frame, per_call, cpf, name, gl))


def perf(tls, v5=False, v6=False):
    """v4/v5/v6: the world-map frame timing (BUILD 605/606b/606c)."""
    if v6:
        r = struct.unpack_from(P6_FMT, tls, 0)
        frames, own, over = r[0], r[1], r[2:7]
        sums, mx, tris_max, tris_sum = r[8:16], r[16:24], r[24], r[26]
        names = ('frame', 'logic+wait', 'window', ' - projection', ' - submit',
                 'far field', 'rest+submit', ' - terrain draw')
        order = (0, 1, 2, 5, 6, 3, 4, 7)
        fmt, fsize, base, nworst = F6_FMT, 48, 144, 2
    elif v5:
        r = struct.unpack_from(P5_FMT, tls, 0)
        frames, own, over = r[0], r[1], r[2:7]
        sums, mx, tris_max, tris_sum = r[8:15], r[15:22], r[22], r[23]
        names = ('frame', 'logic+wait', 'window', ' - projection', ' - submit',
                 'far field', 'rest+submit')
        order = (0, 1, 2, 5, 6, 3, 4)
        fmt, fsize, base, nworst = F5_FMT, 40, 128, 3
    else:
        r = struct.unpack_from(P4_FMT, tls, 0)
        frames, own, over, sums, mx, tris_max, tris_sum = r[0], None, r[1:6], r[6:11], r[11:16], r[16], r[17]
        names = ('frame', 'logic+wait', 'window', 'far field', 'rest+submit')
        order = (0, 1, 2, 3, 4)
        fmt, fsize, base, nworst = F4_FMT, 32, 96, 5
    print()
    if not frames:
        print('world-map timing: no frames measured (is this main the 605+ build?)')
        return 0
    print('world-map timing over %d frames (the first 30 of each visit skipped):' % frames)
    if own is not None:
        print('  frames with worldfar drawing the 5x5 window: %d (%.1f%%)' % (own, 100.0 * own / frames))
    print('  %-14s %9s %9s' % ('', 'avg ms', 'max ms'))
    for n, k in zip(names, order):
        print('  %-14s %9.2f %9.2f' % (n, sums[k] / frames / 1000.0, mx[k] / 1000.0))
    print('  far-field triangles: avg %d, max %d' % (tris_sum // frames, tris_max))
    lims = (16.9, 18.0, 20.0, 25.0, 33.4)
    print('  frames over ' + ', '.join('%.1f ms: %d (%.1f%%)' % (l, o, 100.0 * o / frames)
                                        for l, o in zip(lims, over)))
    print('  the longest frames (ms):')
    hdr = ('frame', 'logic', 'window') + (('proj', 'submit') if v5 else ()) + ('far', 'rest') + (('terr',) if v6 else ())
    print('    ' + ' '.join('%7s' % h for h in hdr) + ' %7s %6s %6s %7s %4s %4s'
          % ('tris', 'draws', 'zoom', 'alt', 'view', 'own'))
    for k in range(nworst):
        f = struct.unpack_from(fmt, tls, base + fsize * k)
        if not f[0]:
            continue
        if v6:
            ms = (f[0], f[1], f[2], f[5], f[6], f[3], f[4], f[7])
            tris, draws, zoom, alt, view = f[8], f[9], f[10], f[11], f[12]
        elif v5:
            ms = (f[0], f[1], f[2], f[5], f[6], f[3], f[4])
            tris, draws, zoom, alt, view = f[7], f[8], f[9], f[10], f[11]
        else:
            ms = f[0:5]
            tris, draws, zoom, alt, view = f[5], f[6], f[7], f[8], f[9]
        print('    ' + ' '.join('%7.2f' % (x / 1e3) for x in ms)
              + ' %7d %6d %6d %7d %4d %4s' % (tris, draws, zoom, 2 * alt, view & 0xFF,
                                            'yes' if view >> 8 else 'no'))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
