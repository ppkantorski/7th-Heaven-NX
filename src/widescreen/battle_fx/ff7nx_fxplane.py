#!/usr/bin/env python3
"""
ff7nx_fxplane.py -- BUILD 682: Refuse's spinning green square covers 16:9.

THE SYMPTOM
===========
Refuse (enemy-attack effect 20; also Isogin Smog, the same effect): a flat
green field spins in front of the camera, and on 16:9 its corners swing in
from the sides, leaving empty triangles (hardware 10-15, after build 680's
tube change, which was a different primitive).

WHY (x86, ff7_en)
=================
Effect 20 spawns 0x55140F at frame 0x1E with slot+4 = (0, 0, 2000) and a
zero rotation. Each frame it draws sprite 5 of SPECIAL/OSEN (osen.s: one
192 x 192 square, offset -96,-96) through 0x55153C, which builds the matrix
from scratch -- RotMatrix(slot+0xC), ScaleMatrix(0x1000, 0x1000, 0x1000),
translation = slot+4 raw, no camera matrix -- so the square is locked to
the camera at depth 2000, centred, rolling by 0x10 a frame (0..84 deg over
its 60 frames).

Measured on the 10-15 video, the square's half side is 1.67 x the screen's
half height: exactly the half diagonal of a 4:3 screen (5/3), so on 4:3 it
covers every corner at every angle. A 16:9 corner is sqrt(16^2+9^2)/9 =
2.04 half heights out, which needs 2.04 / 1.667 = 1.22 x.

THE CHANGE (exefs/main, one word)
=================================
The recompiled `push 0x1000` (the scale argument at x86 0x551451) is
`mov w8, #0x1000` (orr, 0x321403E8) at module +0x5D5B88; it becomes
`movz w8, #SCALE`
(0x1500, x1.31: the 1.22 needed plus margin for the measurement). The
scale is the square's own only, set fresh every frame; nothing else reads
it. The same code (0x46D0F4) also draws Omnislash's closing swirl, a
different sprite at x4/x3 and 8 x farther away; that one is not changed.

SEVENTH_NX_FX_PLANE=0 leaves the word stock.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os as _nx_os, sys as _nx_sys  # BUILD 755: run directly, find src/
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
if _nx_os.path.join(_nx_root, 'src') not in _nx_sys.path:
    _nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install()
import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path

_HERE = nxpaths.ROOT
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

VERSION = 682
ENV = 'SEVENTH_NX_FX_PLANE'
STOCK_SCALE = 0x1000
STOCK_WORD = 0x321403E8         # orr w8, wzr, #0x1000
SCALE = 0x1500
SITE = 0x5D5B88
# movz w20,#0x6fc8 ; movk w20,#0x8a,lsl#16 -- the sprite record 0x8A6FC8
# this function draws, so the site is the right function's word.
ANCHOR = (0x5D5AF8, (0x528DF914, 0x72A01154))
# the words around the site: bl <helper> before, str w8,[x0] and
# ldr w8,[x22,#0x14] after.
AFTER = (0xB9000008, 0xB94016C8)


def enabled(env=None):
    env = os.environ if env is None else env
    return env.get(ENV, '1').strip().lower() not in ('0', 'off', 'no', 'false')


def movz_w8(imm):
    return 0x52800008 | (imm << 5)


def _w(img, va):
    return struct.unpack_from('<I', img, va)[0]


def site_state(img):
    """'stock', 'patched' or a reason string."""
    va, words = ANCHOR
    if tuple(_w(img, va + 4 * i) for i in range(2)) != words:
        return 'sprite record 0x8A6FC8 not loaded at +0x%X' % va
    if (_w(img, SITE - 4) & 0xFC000000) != 0x94000000:
        return 'no helper call before +0x%X' % SITE
    if tuple(_w(img, SITE + 4 + 4 * i) for i in range(2)) != AFTER:
        return 'context after +0x%X changed' % SITE
    w = _w(img, SITE)
    if w == STOCK_WORD:
        return 'stock'
    if w == movz_w8(SCALE):
        return 'patched'
    return 'unexpected word %08X at +0x%X' % (w, SITE)


def apply_to_nso(src, dest, log=print):
    """Write `src` patched to `dest`. True if dest was written."""
    import nso_patcher
    import nxmap
    img = nxmap.Main(str(src)).img
    st = site_state(img)
    if st == 'patched':
        log('    Refuse square: already x%.2f' % (SCALE / 4096))
        return False
    if st != 'stock':
        log('  ! Refuse square: %s -- nothing written' % st)
        return False
    nso = nso_patcher.read_nso(Path(src))
    nso_patcher.apply_spec(nso, {'name': 'fx plane', 'patches': [{
        'name': 'Refuse square scale', 'va': hex(SITE),
        'expect': struct.pack('<I', STOCK_WORD).hex(),
        'set': struct.pack('<I', movz_w8(SCALE)).hex()}]})
    fd, tmp = tempfile.mkstemp(dir=str(Path(dest).parent), prefix='.plane-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(dest))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    log('    Refuse square: x1.00 -> x%.2f (covers 16:9 at every angle)'
        % (SCALE / 4096))
    return True


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('src')
    ap.add_argument('dest')
    a = ap.parse_args()
    raise SystemExit(0 if apply_to_nso(a.src, a.dest) else 1)
