#!/usr/bin/env python3
"""
battleprobe.py -- what a stalled battle is waiting for. DIAGNOSTIC, on a BUILT
main. No rebuild.

    python3 battleprobe.py sdout/atmosphere/contents/0100A5B00BDC6000/exefs/main --on
    python3 battleprobe.py <same main> --off          (or just rebuild)

USE
===
1. `--on`, copy that exefs/main to the Switch (nothing else changes).
2. Reproduce the stall (Vincent's Chaos vs a Master Tonberry).
3. While it is stuck, in battle, press L3 (click the left stick). The game closes on purpose and
   Atmosphere writes atmosphere/crash_reports/<time>_0100a5b00bdc6000.log.
4. `python3 battleprobe_read.py <that .log>` -- or send the .log.
5. `--off` (puts the main from before --on back), copy it to the Switch.

WHAT IT RECORDS (native/battleprobe.c)
======================================
Only on L3 (left stick click) in battle: every live slot of the four battle effect lists
(effect10 movement, camera, effect60 per-frame effects, effect100 actions),
each with its function and its counters; the animation event queue; the
battle AI context head; every actor's status and state flags; the pause and
script-wait bytes. Nothing runs on any other frame but the L3 test.

WHERE IT GOES
=============
The gfx_drv_flip entry (+0x10DA880; texprobe/leakprobe must be off), and a
stretch of the proven-dead x86 0x623D28 body that is still the STOCK bytes
in this main -- found at --on time by comparing with the dump's main, so it
never lands on a cave a pass put there. No BSS, no table space.

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
sys.path.insert(0, HERE)

import a64 as A                              # noqa: E402
import ff7nx_audio_cave as AC                # noqa: E402
import ff7nx_deadspace as DS                 # noqa: E402

BLOB = os.path.join(HERE, 'native', 'battleprobe.bin')
ENTRY = os.path.join(HERE, 'native', 'battleprobe.entry')
FLIP = 0x10DA880                   # native gfx_drv_flip
FLIP_WORD = 0xFC1C0FE8             # str d8, [sp, #-0x40]!
G2H = 0x10FC3A0
INPUT_GOT = 0x12CE1D0
MODE_SLOT = 0x12CE1F8
STOCK_CANDIDATES = ('dump/exefs/main', 'exefs/main')


def entry_offset():
    for line in open(ENTRY):
        name, off = line.split()
        if name == 'bp_frame':
            return int(off, 16)
    raise ValueError('bp_frame missing from %s' % ENTRY)


def _addr(w, reg, at, value):
    w.append(A.adrp(reg, at + 4 * len(w), value & ~0xFFF))
    w.append(A.add_imm64(reg, reg, value & 0xFFF))


def frame_stub(at, fn):
    w = [A.stp64_pre(29, 30, 31, -0x60),
         A.stp64_off(0, 1, 31, 0x10), A.stp64_off(2, 3, 31, 0x20),
         A.stp64_off(4, 5, 31, 0x30), A.stp64_off(6, 7, 31, 0x40),
         A.str64(8, 31, 0x50)]
    _addr(w, 0, at, G2H)
    _addr(w, 1, at, INPUT_GOT)
    _addr(w, 2, at, MODE_SLOT)
    w.append(A.bl(at + 4 * len(w), fn))
    w += [A.ldr64(8, 31, 0x50),
          A.ldp64_off(6, 7, 31, 0x40), A.ldp64_off(4, 5, 31, 0x30),
          A.ldp64_off(2, 3, 31, 0x20), A.ldp64_off(0, 1, 31, 0x10),
          A.ldp64_post(29, 30, 31, 0x60), FLIP_WORD]
    w.append(A.b(at + 4 * len(w), FLIP + 4))
    return w


def stock_path(explicit=None):
    if explicit:
        return explicit
    for rel in STOCK_CANDIDATES:
        p = os.path.join(HERE, rel)
        if os.path.isfile(p):
            return p
    raise ValueError('no stock exefs/main found (looked for %s)'
                     % ', '.join(STOCK_CANDIDATES))


def find_span(text, stock_text, lo, hi, need):
    """Lowest 16-aligned run of still-stock bytes in [lo, hi) of `need`."""
    at = (lo + 0x40 + 15) & ~15        # never the dead function's entry
    while at + need <= hi:
        bad = None
        for i in range(at, at + need, 4):
            if text[i:i + 4] != stock_text[i:i + 4]:
                bad = i
                break
        if bad is None:
            return at
        at = (bad + 4 + 15) & ~15
    raise ValueError('no %d-byte stock stretch left in the dead region' % need)


def apply(src, dest, stock):
    blob = open(src, 'rb').read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    got = struct.unpack_from('<I', text, FLIP)[0]
    if got != FLIP_WORD:
        raise ValueError('gfx_drv_flip entry +0x%X is %08X, expected %08X -- '
                         'another probe owns it; --off that first'
                         % (FLIP, got, FLIP_WORD))
    sblob = open(stock, 'rb').read()
    _ss, sraw = AC.segments(sblob)
    stock_text = sraw[0]
    lo, hi = DS.region(stock)
    code = open(BLOB, 'rb').read()
    stub_words = len(frame_stub(0, 0x1000))
    need = ((4 * stub_words + 15) & ~15) + len(code)
    stub = find_span(text, stock_text, lo, hi, need)
    entry = (stub + 4 * stub_words + 15) & ~15
    for i, w in enumerate(frame_stub(stub, entry + entry_offset())):
        struct.pack_into('<I', text, stub + 4 * i, w)
    text[entry:entry + len(code)] = code
    struct.pack_into('<I', text, FLIP, A.b(FLIP, stub))
    raw[0] = bytes(text)
    out = AC.pack(blob, raw, 0)
    _cs, craw = AC.segments(out)
    if craw[0][entry:entry + len(code)] != code or \
            struct.unpack_from('<I', craw[0], FLIP)[0] != A.b(FLIP, stub):
        raise ValueError('the probe did not survive the repack')
    open(dest, 'wb').write(out)
    return {'stub': stub, 'entry': entry, 'code': len(code), 'need': need}


def main(argv):
    if len(argv) < 3 or argv[2] not in ('--on', '--off'):
        print(__doc__)
        return 2
    path = argv[1]
    backup = path + '.pre-battleprobe'
    if argv[2] == '--off':
        if not os.path.exists(backup):
            print('  no %s -- rebuild instead' % os.path.basename(backup))
            return 1
        shutil.copyfile(backup, path)
        os.remove(backup)
        print('  battleprobe -> off (the main from before --on is back)')
        return 0
    if os.path.exists(backup):
        print('  already on (%s exists); --off first' % os.path.basename(backup))
        return 1
    stock = stock_path(argv[3] if len(argv) > 3 else None)
    shutil.copyfile(path, backup)
    try:
        r = apply(backup, path, stock)
    except Exception:
        shutil.copyfile(backup, path)
        os.remove(backup)
        raise
    print('  battleprobe -> on: stub +0x%X, code +0x%X (%d bytes) in still-'
          'stock dead space; no BSS' % (r['stub'], r['entry'], r['code']))
    print('  copy exefs/main to the Switch, reproduce the stall, press L3 (left stick) in '
          'the stuck battle, then run battleprobe_read.py on the crash report')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
