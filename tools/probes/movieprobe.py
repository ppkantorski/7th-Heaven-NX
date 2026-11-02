#!/usr/bin/env python3
"""
movieprobe.py -- BUILD 877m. Does an FMV busy-wait? DIAGNOSTIC, on a BUILT
main. No rebuild.

    python3 tools/probes/movieprobe.py <exefs/main> --on [stock main]
    python3 tools/probes/movieprobe.py <exefs/main> --off

USE
===
1. `--on`, copy that exefs/main to the Switch.
2. Start a cutscene movie (an FMV). While it plays, click L3 once: nothing
   visible, the probe starts counting. Wait 3-5 seconds.
3. Click L3 again, still during the movie. The game closes on purpose and
   Atmosphere writes atmosphere/crash_reports/<time>_0100a5b00bdc6000.log.
4. Send the .log.
5. `--off` (or rebuild).

WHAT IT MEASURES (native/movieprobe.c)
=====================================
During a movie no frame limiter runs (the field limiter skips itself, see
ff7nx_moviepace). fw_movie_update (+0x10F1590) waits for the decoder's
next frame in a loop (+0x10F161C..+0x10F1648) that calls present()
(+0x10F9080) on every pass. If present blocks on vsync, the loop sleeps;
if it returns at once, the loop spins. The probe wraps that present call
and counts the passes, the time inside present, the longest one, and how
many took under 1 ms.

    x0 'MVP1' | flips<<32        x1 presents in the wait | movie updates<<32
    x2 ticks inside them (19.2 MHz)   x3 ticks since armed
    x4 longest present | presents under 1 ms<<32
    x5 main thread CPU ticks (svcGetInfo ThreadTickCount)
    x6 idle ticks of the core armed on   x7 core | core now<<8 | waits<<32

WHERE IT GOES
=============
The timing probe's dead body (ff7nx_frameprobe.own_region; frameprobe and
the other probes must be off). Hooks: the present call at +0x10F1640, the
entry of fw_movie_update, and gfx_drv_flip's entry. 128 bytes of new BSS.

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
import os
import shutil
import struct
import sys

HERE = nxpaths.ROOT
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import a64 as A                              # noqa: E402
import ff7nx_audio_cave as AC                # noqa: E402
import battleprobe as BP                     # noqa: E402

BLOB = os.path.join(HERE, 'native', 'movieprobe.bin')
ENTRY = os.path.join(HERE, 'native', 'movieprobe.entry')
STATE_BYTES = 128                  # >= sizeof(P), 88

UPDATE = 0x10F1590                 # fw_movie_update
UPDATE_WORD = 0xF81C0FF7           # str x23, [sp, #-0x40]!
PRESENT_CALL = 0x10F1640           # bl present, inside the wait loop
PRESENT = 0x10F9080


def entries():
    out = {}
    for line in open(ENTRY):
        name, off = line.split()
        out[name] = int(off, 16)
    return out


def _save(w):
    w += [A.stp64_pre(29, 30, 31, -0x60),
          A.stp64_off(0, 1, 31, 0x10), A.stp64_off(2, 3, 31, 0x20),
          A.stp64_off(4, 5, 31, 0x30), A.stp64_off(6, 7, 31, 0x40),
          A.str64(8, 31, 0x50)]


def _restore(w):
    w += [A.ldr64(8, 31, 0x50),
          A.ldp64_off(6, 7, 31, 0x40), A.ldp64_off(4, 5, 31, 0x30),
          A.ldp64_off(2, 3, 31, 0x20), A.ldp64_off(0, 1, 31, 0x10),
          A.ldp64_post(29, 30, 31, 0x60)]


def frame_stub(at, state, fn):
    w = []
    _save(w)
    BP._addr(w, 0, at, state)
    BP._addr(w, 1, at, BP.INPUT_GOT)
    w.append(A.bl(at + 4 * len(w), fn))
    _restore(w)
    w.append(BP.FLIP_WORD)
    w.append(A.b(at + 4 * len(w), BP.FLIP + 4))
    return w


def update_stub(at, state, fn):
    w = []
    _save(w)
    BP._addr(w, 0, at, state)
    w.append(A.bl(at + 4 * len(w), fn))
    _restore(w)
    w.append(UPDATE_WORD)
    w.append(A.b(at + 4 * len(w), UPDATE + 4))
    return w


def present_stub(at, state, fn, callee=PRESENT):
    """bl here in place of bl present: mp_present(state, present). present
    takes no arguments and nothing reads x0..x18 after it."""
    w = [A.stp64_pre(29, 30, 31, -0x10)]
    BP._addr(w, 0, at, state)
    BP._addr(w, 1, at, callee)
    w.append(A.bl(at + 4 * len(w), fn))
    w.append(A.ldp64_post(29, 30, 31, 0x10))
    w.append(0xD65F03C0)            # ret
    return w


def layout(at, state, fn, callee=PRESENT):
    stubs, words = {}, {}

    def put(name, ws):
        nonlocal at
        stubs[name] = at
        for i, x in enumerate(ws):
            words[at + 4 * i] = x
        at += 4 * len(ws)

    put('frame', frame_stub(at, state, fn['mp_frame']))
    put('update', update_stub(at, state, fn['mp_update']))
    put('present', present_stub(at, state, fn['mp_present'], callee))
    return stubs, words, at


def apply(src, dest, stock):
    blob = open(src, 'rb').read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    for va, want, what in ((BP.FLIP, BP.FLIP_WORD, 'gfx_drv_flip entry'),
                           (UPDATE, UPDATE_WORD, 'fw_movie_update entry')):
        got = struct.unpack_from('<I', text, va)[0]
        if got != want:
            raise ValueError('%s +0x%X is %08X, not %08X -- another probe '
                             'owns it' % (what, va, got, want))
    # 877m2: present, or the movie wait sleep's cave (present + sleep)
    got = struct.unpack_from('<I', text, PRESENT_CALL)[0]
    if got >> 26 != 0x25:
        raise ValueError('+0x%X is %08X, not a bl' % (PRESENT_CALL, got))
    callee = PRESENT_CALL + ((((got & 0x3FFFFFF) ^ 0x2000000) - 0x2000000) << 2)
    import ff7nx_frameprobe as FP
    lo, hi = FP.own_region(src, stock)
    code = open(BLOB, 'rb').read()
    rel = entries()
    base = AC.scratch_base(blob, segs)
    state = (base + 15) & ~15
    growth = (state - base) + STATE_BYTES + AC.bss_tail_slack(segs)
    entry = (lo + 15) & ~15
    fn = {k: entry + v for k, v in rel.items()}
    stubs, words, end = layout((entry + len(code) + 15) & ~15, state, fn, callee)
    if end > hi:
        raise ValueError('movieprobe: %d bytes do not fit the %d-byte body'
                         % (end - lo, hi - lo))
    text[entry:entry + len(code)] = code
    for va, x in words.items():
        struct.pack_into('<I', text, va, x)
    struct.pack_into('<I', text, BP.FLIP, A.b(BP.FLIP, stubs['frame']))
    struct.pack_into('<I', text, UPDATE, A.b(UPDATE, stubs['update']))
    struct.pack_into('<I', text, PRESENT_CALL,
                     A.bl(PRESENT_CALL, stubs['present']))
    raw[0] = bytes(text)
    out = AC.pack(blob, raw, growth)
    _cs, craw = AC.segments(out)
    if craw[0][entry:entry + len(code)] != code:
        raise ValueError('the probe did not survive the repack')
    open(dest, 'wb').write(out)
    return {'entry': entry, 'bytes': end - entry, 'state': state,
            'growth': growth, 'stubs': stubs}


def read(path):
    """Decode a crash report's registers."""
    import frameprobe_read as FR
    r = FR.parse_registers(open(path, errors='replace').read())
    x = [r.get(i, 0) for i in range(8)]
    if x[0] & 0xFFFFFFFF != 0x3150564D:
        return 'not a movieprobe report (x0 = %016X)' % x[0]
    cps = 19200000.0
    flips, pres, upd = x[0] >> 32, x[1] & 0xFFFFFFFF, x[1] >> 32
    ticks, el, maxt, fast = x[2], x[3], x[4] & 0xFFFFFFFF, x[4] >> 32
    thr, idle, waits = x[5], x[6], x[7] >> 32
    c0, c1 = x[7] & 0xFF, (x[7] >> 8) & 0xFF
    s = el / cps
    lines = ['%.2f s armed' % s,
             'flips %d (%.1f/s), movie updates %d (%.1f/s), updates that waited %d'
             % (flips, flips / s, upd, upd / s, waits),
             'presents in the wait %d (%.1f/s), %.1f%% of the time inside them'
             % (pres, pres / s, 100.0 * ticks / max(el, 1)),
             'average %.2f ms, longest %.2f ms, under 1 ms: %d'
             % (ticks / max(pres, 1) / cps * 1000, maxt / cps * 1000, fast),
             'main thread CPU %s of the time; core %d (now %d) idle %s'
             % ('?' if thr == (1 << 64) - 1 else '%.1f%%' % (100.0 * thr / max(el, 1)),
                c0, c1,
                '?' if idle == (1 << 64) - 1 else '%.1f%%' % (100.0 * idle / max(el, 1)))]
    return '\n'.join(lines)


def main(argv):
    if len(argv) == 3 and argv[1] == '--read':
        print(read(argv[2]))
        return 0
    if len(argv) < 3 or argv[2] not in ('--on', '--off'):
        print(__doc__)
        return 2
    path = argv[1]
    backup = path + '.pre-movieprobe'
    if argv[2] == '--off':
        if not os.path.exists(backup):
            print('  no %s -- rebuild instead' % os.path.basename(backup))
            return 1
        shutil.copyfile(backup, path)
        os.remove(backup)
        print('  movieprobe -> off')
        return 0
    if os.path.exists(backup):
        print('  already on (%s exists); --off first' % os.path.basename(backup))
        return 1
    stock = BP.stock_path(argv[3] if len(argv) > 3 else None)
    shutil.copyfile(path, backup)
    try:
        r = apply(backup, path, stock)
    except Exception:
        shutil.copyfile(backup, path)
        os.remove(backup)
        raise
    print('  movieprobe -> on: code +0x%X (%d bytes), state +0x%X (BSS +%d)'
          % (r['entry'], r['bytes'], r['state'], r['growth']))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
