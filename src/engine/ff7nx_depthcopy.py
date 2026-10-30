#!/usr/bin/env python3
"""
ff7nx_depthcopy.py -- BUILD 860h. The driver's field depth copy draws with no
colour target, as it means to, instead of with whatever colour target the
pass before it left bound.

THE BUG (mogu_1, hardware 10-22 .. 10-29)
=========================================
While the opening narration is up, the stream at the bottom of mogu_1 stops
at a straight vertical line at 3/4 of the screen width; it fills in the
moment Mog walks out of his house.

MEASURED (BUILD 860g GL probe v2: per-draw state, render target and caller
chain; hardware 10-29, one capture with the cut and one after Mog is out)
=========================================================================
With the cut, the driver's per-draw render-target step (+0x10DCD90, mode 2
case +0x10DCF3C) runs two passes back to back:

  +0x10DEB00  resolve the field buffer onto the screen target; leaves the
              SCREEN colour target (1920x1080) bound as RT0
  +0x10DECA0  copy the depth back into the field buffer's depth surface
              ([0x12CE5B8], 2560x1440) with one full-surface quad, depth
              ALWAYS + write (draw at +0x10DEF6C)

+0x10DECA0 asks for no colour target: SetRenderTargets(2, {NULL, NULL},
depth) (device vtable +0x180, +0x1137540). But that function only binds the
entries that are NOT null -- a NULL array is what unbinds them -- so RT0 is
still the 1080-row screen target. The quad then:
  * gets a viewport flipped with the colour target's height (glViewport
    wrapper +0x1137640): 2560x1440 at y = 1080 - 1440 = -360, and
  * renders into a framebuffer whose colour (1920x1080) and depth
    (2560x1440) differ in size, so GL limits it to their intersection,
    the bottom-left 1920x1080 -- 3/4 of the width.
So the field depth is only rewritten in part, and the water (LEQUAL, drawn
next) passes on the left 3/4 and fails on the right: the cut.

With Mog on screen the models are drawn in mode 1 first (+0x10DCE68), the
field colour target is bound when +0x10DECA0 runs, its size matches the
depth surface, and the copy covers everything (probe: 2560x1440 at (0,0)).
That is why the field "fixes itself" when Mog walks out.

THE FIX
=======
One word. At +0x10DED50 the array pointer for that call

    sub  x2, x29, #0x50        ->   mov  x2, xzr

so the call is SetRenderTargets(2, NULL, depth): the device's own
"depth only" path (+0x11375B4: set the depth surface, then unbind colour
targets 0..3), the same one the per-draw step uses for depth-only draws
(+0x10DD02C). The copy quad then draws into the depth surface alone, with
the depth surface's own size for its viewport. Every later draw binds its
own targets again (+0x10DD004), so nothing after it depends on what this
pass left bound.

SEVENTH_NX_DEPTH_COPY_FIX=0 leaves it stock.

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
import tempfile
from pathlib import Path

VERSION = 8608
ENV = 'SEVENTH_NX_DEPTH_COPY_FIX'

SITE = 0x10DED50
STOCK_WORD = 0xD10143A2           # sub x2, x29, #0x50   (-> {NULL, NULL})
FIX_WORD = 0xAA1F03E2             # mov x2, xzr          (NULL: unbind all)
# the call around it: ldr x3,[x25] / ldr x8,[x8,#0x180] / mov w1,#2 / .. / blr x8
CONTEXT = {0x10DED44: 0xF9400323, 0x10DED48: 0xF940C108,
           0x10DED4C: 0x321F03E1, 0x10DED54: 0xD63F0100}
NULL_PATH = {0x11375B4: 0xF9400268}   # SetRenderTargets: rts == NULL branch


def enabled(env=None):
    env = os.environ if env is None else env
    return env.get(ENV, '1').strip().lower() not in ('0', 'off', 'no', 'false')


def _w(img, va):
    return struct.unpack_from('<I', img, va)[0]


def state(img):
    """'stock', 'fixed' or a problem string."""
    for va, want in list(CONTEXT.items()) + list(NULL_PATH.items()):
        if _w(img, va) != want:
            return '+0x%X is %08X, not %08X' % (va, _w(img, va), want)
    w = _w(img, SITE)
    if w == STOCK_WORD:
        return 'stock'
    if w == FIX_WORD:
        return 'fixed'
    return '+0x%X is %08X, neither stock nor fixed' % (SITE, w)


def apply_to_nso(src, dest, log=print):
    """Write `src` patched to `dest`. True if dest was written."""
    import nso_patcher
    import nxmap
    st = state(nxmap.Main(str(src)).img)
    if st == 'fixed':
        log('    depth copy: already')
        return False
    if st != 'stock':
        log('  ! depth copy: ' + st)
        log('  depth copy: nothing written')
        return False
    nso = nso_patcher.read_nso(Path(src))
    nso_patcher.apply_spec(nso, {'name': 'depth copy', 'patches': [{
        'name': 'depth copy: no colour target', 'va': hex(SITE),
        'expect': struct.pack('<I', STOCK_WORD).hex(),
        'set': struct.pack('<I', FIX_WORD).hex()}]})
    fd, tmp = tempfile.mkstemp(dir=str(Path(dest).parent), prefix='.dcopy-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(dest))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    log('    depth copy: +0x%X SetRenderTargets(2, NULL, depth) -- the field '
        'depth copy no longer draws through the screen colour target' % SITE)
    return True


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('src')
    ap.add_argument('dest')
    a = ap.parse_args()
    raise SystemExit(0 if apply_to_nso(a.src, a.dest) else 1)
