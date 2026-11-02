#!/usr/bin/env python3
"""
lightprobe.py -- BUILD 876p. Why Cloud's model flickers in gaia_*. DIAGNOSTIC,
on a BUILT main (FFNx lighting on, the default). No rebuild.

    python3 tools/probes/lightprobe.py <exefs/main> --on [stock main]
    python3 tools/probes/lightprobe.py <exefs/main> --off

USE
===
1. `--on`, copy that exefs/main to the Switch.
2. In gaia_1, stand still where Cloud flickers. Click L3 (left stick) once:
   nothing visible, the probe starts counting. Wait about 5 seconds.
3. Click L3 again. The game closes on purpose and Atmosphere writes
   atmosphere/crash_reports/<time>_0100a5b00bdc6000.log.
4. `python3 tools/probes/lightprobe_read.py <that .log>` -- or send the .log.
5. `--off` (or rebuild).

WHAT IT RECORDS (native/lighting.c with -DLT_PROBE, native/lightprobe.bin)
=========================================================================
The model lighting (ff7nx_lighting, BUILD 679) with counters: for every
polygon set the field relights (x86 0x64CA65 / 0x64C382, gaia_1's KAWAI
sub-op 6 loop), whether the relight found the set's own part record (679),
fell back to the light's last setup, or took the stock path, and how much
its first lit vertex changes from one relight to the next. Plus the
binding at draw time (0x68E92D) and the setup count.

WHERE IT GOES
=============
A fresh copy of the lighting code and stubs in the timing probe's own
dead body (x86 0x66ED3D, ff7nx_frameprobe.own_region; frameprobe must be
off); all
eight lighting sites and the gfx_drv_flip entry are pointed at it. The
lighting state moves to new BSS (LtAll + 2 KB for the probe); the old
copy is left unreferenced.

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
import ff7nx_deadspace as DS                 # noqa: E402
import ff7nx_lighting as L                   # noqa: E402
import battleprobe as BP                     # noqa: E402

BLOB = os.path.join(HERE, 'native', 'lightprobe.bin')
ENTRY = os.path.join(HERE, 'native', 'lightprobe.entry')
PROBE_BYTES = 2048                 # >= sizeof(Probe) in lighting.c (LT_PROBE), 1296


def entries():
    out = {}
    for line in open(ENTRY):
        name, off = line.split()
        out[name] = int(off, 16)
    return out


def frame_stub(at, state, fn):
    """at gfx_drv_flip's entry: lp_frame(state, input got), every register
    the flip reads kept, then the displaced word and back."""
    w = [A.stp64_pre(29, 30, 31, -0x60),
         A.stp64_off(0, 1, 31, 0x10), A.stp64_off(2, 3, 31, 0x20),
         A.stp64_off(4, 5, 31, 0x30), A.stp64_off(6, 7, 31, 0x40),
         A.str64(8, 31, 0x50)]
    BP._addr(w, 0, at, state)
    BP._addr(w, 1, at, BP.INPUT_GOT)
    w.append(A.bl(at + 4 * len(w), fn))
    w += [A.ldr64(8, 31, 0x50),
          A.ldp64_off(6, 7, 31, 0x40), A.ldp64_off(4, 5, 31, 0x30),
          A.ldp64_off(2, 3, 31, 0x20), A.ldp64_off(0, 1, 31, 0x10),
          A.ldp64_post(29, 30, 31, 0x60), BP.FLIP_WORD]
    w.append(A.b(at + 4 * len(w), BP.FLIP + 4))
    return w


def layout(at, state, fn):
    stubs, words = {}, {}

    def put(name, ws):
        nonlocal at
        stubs[name] = at
        for i, x in enumerate(ws):
            words[at + 4 * i] = x
        at += 4 * len(ws)

    put('setup', L.setup_stub(at, state, fn))
    put('vtx', L.vtx_stub(at, state, fn))
    put('mv', L.mv_stub(at, state, fn))
    put('ecommon', L.entry_common(at, state, fn))
    for site, kind, what in L.ENTRY_SITES:
        # 876p2: 0x64C382 relights as kind 2, so the probe can tell the two apart
        k = 2 if kind and site == L.ENTRY_SITES[2][0] else kind
        put('entry%X' % site, L.entry_stub(at, site, k, stubs['ecommon']))
    put('frame', frame_stub(at, state, fn['lp_frame']))
    return stubs, words, at


def apply(src, dest, stock):
    blob = open(src, 'rb').read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    got = struct.unpack_from('<I', text, BP.FLIP)[0]
    if got != BP.FLIP_WORD:
        raise ValueError('gfx_drv_flip entry +0x%X is %08X, not %08X -- another '
                         'probe owns it' % (BP.FLIP, got, BP.FLIP_WORD))
    for site, target, what in L.SITES:
        w = struct.unpack_from('<I', text, site)[0]
        if w >> 26 != 0x25:
            raise ValueError('lighting: +0x%X (%s) is %08X, not a bl' % (site, what, w))
    for site, kind, what in L.ENTRY_SITES:
        w = struct.unpack_from('<I', text, site)[0]
        if w != L.ENTRY_WORD and w >> 26 != 0x05:
            raise ValueError('lighting: +0x%X (%s) is %08X' % (site, what, w))
    sblob = open(stock, 'rb').read()
    _ss, sraw = AC.segments(sblob)
    import ff7nx_frameprobe as FP
    lo, hi = FP.own_region(src, stock)     # the timing probe's own dead body
    code = open(BLOB, 'rb').read()
    rel = entries()
    base = AC.scratch_base(blob, segs)
    state = (base + 15) & ~15
    growth = (state - base) + L.STATE_BYTES + PROBE_BYTES + AC.bss_tail_slack(segs)
    _s, _w, end = layout(0, state, {k: 0x1000 + v for k, v in rel.items()})
    need = ((len(code) + 15) & ~15) + end
    entry = (lo + 15) & ~15
    if entry + need > hi:
        raise ValueError('lightprobe: %d bytes do not fit the %d-byte body' % (need, hi - lo))
    fn = {k: entry + v for k, v in rel.items()}
    stubs, words, end = layout((entry + len(code) + 15) & ~15, state, fn)
    assert end <= entry + need
    text[entry:entry + len(code)] = code
    for va, x in words.items():
        struct.pack_into('<I', text, va, x)
    struct.pack_into('<I', text, L.SETUP_CALL, A.bl(L.SETUP_CALL, stubs['setup']))
    struct.pack_into('<I', text, L.VTX_CALL, A.bl(L.VTX_CALL, stubs['vtx']))
    for s in L.MV_CALLS:
        struct.pack_into('<I', text, s, A.bl(s, stubs['mv']))
    for site, kind, what in L.ENTRY_SITES:
        struct.pack_into('<I', text, site, A.b(site, stubs['entry%X' % site]))
    struct.pack_into('<I', text, BP.FLIP, A.b(BP.FLIP, stubs['frame']))
    raw[0] = bytes(text)
    out = AC.pack(blob, raw, growth)
    _cs, craw = AC.segments(out)
    if craw[0][entry:entry + len(code)] != code:
        raise ValueError('the probe did not survive the repack')
    open(dest, 'wb').write(out)
    return {'entry': entry, 'bytes': end - entry, 'state': state, 'growth': growth}


def main(argv):
    if len(argv) < 3 or argv[2] not in ('--on', '--off'):
        print(__doc__)
        return 2
    path = argv[1]
    backup = path + '.pre-lightprobe'
    if argv[2] == '--off':
        if not os.path.exists(backup):
            print('  no %s -- rebuild instead' % os.path.basename(backup))
            return 1
        shutil.copyfile(backup, path)
        os.remove(backup)
        print('  lightprobe -> off (the main from before --on is back)')
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
    print('  lightprobe -> on: code +0x%X (%d bytes, the timing probe body), '
          'state +0x%X (BSS +%d)' % (r['entry'], r['bytes'], r['state'], r['growth']))
    print('  copy exefs/main to the Switch; in gaia_1 click L3 once, wait ~5 s, '
          'click L3 again; send the crash report')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
