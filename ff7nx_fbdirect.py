#!/usr/bin/env python3
r"""
ff7nx_fbdirect.py -- read the SCENE TARGET directly, not the capture surface.

TWO WORDS, IN PLACE, NO CAVE.

WHY
===
Measured on hardware (FINDINGS 342/343/354, corrected by 352):

    the capture surface is 2560 x 1920 at k = 4, stride ~2816   (confirmed)
    the resample's geometry and addressing are correct          (confirmed)
    the capture blit covers the WHOLE surface with the WHOLE
        source -- dest rect {0,0,W,H}, viewport and scissor the
        same rect, quad UVs 0,0-1,1                             (read from
                                                                 the binary)
    and yet only a 150 x 1150 block of that surface holds a
        picture. 88% of it is pure black                        (measured, two
                                                                 independent
                                                                 probes)

Those cannot all be true, and every one of them has been checked. Whatever is
wrong is inside the blit or its source, behind vtable thunks that cannot be
resolved statically.

So stop using the capture surface. The frame the effect wants is already in
the SCENE TARGET, at full display resolution, before the blit ever runs.

THE SYMMETRY THAT MAKES IT TWO WORDS
====================================
The two surfaces are created by the same call, with the same flags, and their
handles are installed in the same shape 0x10 apart:

    +10D58BC  bl #0x44e0        the SCENE TARGET, display resolution
    +10D58CC  str w0, [x24]         its id     -> [[0x12CE610]]
    +10D593C  str x0, [x25]         its object -> [[0x12CE618]]

    +10D5984  bl #0x44e0        the CAPTURE SURFACE, literal 640x480
    +10D5994  str w0, [x8]          its id     -> [[0x12CE620]]
    +10D5A04  str x0, [x21]         its object -> [[0x12CE628]]

Both are created with `mov w1, #1`, i.e. identical usage flags, so if one is
CPU-mappable the other is too. That is what makes this swap plausible rather
than reckless.

The resample loader (+0x10D7050) takes its two inputs from the capture pair:

    +10D7090  ldr x21, [x21, #0x628]    the object whose PIXELS are mapped
    +10D70DC  ldr x25, [x25, #0x620]    the id whose WIDTH/HEIGHT/STRIDE
                                        are read (vtable +0x28/+0x40/+0x30)

Moving BOTH to the scene target's pair keeps them consistent with each other
-- which is the whole safety argument. Redirecting one without the other
would read one surface's bytes with another's geometry, which is the class of
bug this project has already been bitten by.

WHAT IT CHANGES, AND WHAT IT DOES NOT
=====================================
  * the resample reads the live frame instead of a copy of it
  * the geometry follows, because it comes from the same handle
  * `ff7nx_fbsurf`'s k stops mattering for the PIXELS: the scene target is
    whatever the console renders at. k still sizes the (now unused) capture
    surface, so leave it alone rather than churn another variable
  * fb_tex.x / .y and the 640/854 step are unchanged, so the FRAMING will
    move: 320 was 12.5% into a 2560-wide surface and is 25% into a 1280-wide
    one. Expect the field to be displaced until that is re-fitted. THIS IS
    EXPECTED and it is the next thing to measure, not a failure

BLAST RADIUS
============
+0x10D7050 is the shared CPU loader: every 1:1 framebuffer capture in the
game goes through it, not just Kujata's. All of them are copies of the same
frame, so reading the source instead of the copy should be equivalent or
better for all -- but it IS every one of them, and that is why this is
opt-in and off by default.

If the scene target turns out not to be mappable the loader gets a null
pointer. That is the one real risk and it is why this ships behind
`SEVENTH_NX_FB_DIRECT` rather than as a default.
"""
import argparse
import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import nxmap                                                   # noqa: E402

DIRECT_ENV = 'SEVENTH_NX_FB_DIRECT'

# the two loads, and the handles they choose between
PIX_SITE = 0x10D7090        # ldr x21, [x21, #0x628]   capture object
PIX_STOCK = 0xF94316B5
PIX_DIRECT = 0xF9430EB5     # ldr x21, [x21, #0x618]   scene target object

GEO_SITE = 0x10D70DC        # ldr x25, [x25, #0x620]   capture id
GEO_STOCK = 0xF9431339
GEO_DIRECT = 0xF9430B39     # ldr x25, [x25, #0x610]   scene target id

# Everything the two words depend on. If the loader moves, these stop matching
# and the module refuses rather than writing into whatever is there now.
ANCHORS = {
    0x10D7050: 0xD10403FF,   # sub  sp, sp, #0x100        the loader's prologue
    0x10D7094: 0xF9400100,   # ldr  x0, [x8]
    0x10D709C: 0xAA0103F4,   # mov  x20, x1               the tex_header
    0x10D70E4: 0xB9800328,   # ldrsw x8, [x25]            the id
    0x10D70FC: 0xF8687928,   # ldr  x8, [x9, x8, lsl #3]  table[id]
    0x10D710C: 0xD63F0100,   # blr  x8                    GetWidth
    0x10D7134: 0xD63F0100,   # blr  x8                    GetHeight
    0x10D7180: 0xD63F0100,   # blr  x8                    the stride
    0x10D7184: 0x2A0003F3,   # mov  w19, w0
    0x10D71A4: 0xD63F0100,   # blr  x8                    map -> x26
    0x10D71A8: 0xF94013FA,   # ldr  x26, [sp, #0x20]
    0x10D71F0: 0xAA0B03EE,   # mov  x14, x11              ff7nx_fbresample's hook
}


def enabled() -> bool:
    return os.environ.get(DIRECT_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def words_for(direct: bool) -> dict:
    """The two words, in site order. `direct` False reproduces the stock
    image exactly."""
    if direct:
        return {PIX_SITE: PIX_DIRECT, GEO_SITE: GEO_DIRECT}
    return {PIX_SITE: PIX_STOCK, GEO_SITE: GEO_STOCK}


assert words_for(False) == {PIX_SITE: PIX_STOCK, GEO_SITE: GEO_STOCK}


def verify(img) -> list:
    bad = []
    for va, want in sorted(ANCHORS.items()):
        got = _word(img, va)
        if got != want:
            bad.append('anchor +0x%X is %08X, expected %08X'
                       % (va, got, want))
    for va, (a, b) in ((PIX_SITE, (PIX_STOCK, PIX_DIRECT)),
                       (GEO_SITE, (GEO_STOCK, GEO_DIRECT))):
        got = _word(img, va)
        if got not in (a, b):
            bad.append('+0x%X is %08X, which is neither the stock load nor '
                       'the redirected one' % (va, got))
    return bad


def read_state(img) -> str:
    p, g = _word(img, PIX_SITE), _word(img, GEO_SITE)
    if (p, g) == (PIX_STOCK, GEO_STOCK):
        return 'capture surface (stock)'
    if (p, g) == (PIX_DIRECT, GEO_DIRECT):
        return 'scene target (direct)'
    return 'MIXED -- pixels and geometry disagree'


def plan(m, revert=False):
    img = m.img
    problems = verify(img)
    if problems:
        return [], [], problems
    want = words_for(False if revert else enabled())
    patches, notes = [], []
    for va in sorted(want):
        have = _word(img, va)
        if have == want[va]:
            continue
        patches.append({'name': 'fb source %s'
                        % ('pixels' if va == PIX_SITE else 'geometry'),
                        'va': hex(va),
                        'expect': struct.pack('<I', have).hex(),
                        'set': struct.pack('<I', want[va]).hex()})
    if patches:
        notes.append('    resample source: %s -> %s'
                     % (read_state(img),
                        'scene target (direct)' if (not revert and enabled())
                        else 'capture surface (stock)'))
    return patches, notes, []


def _write(main, patches, log):
    import nso_patcher
    main = Path(main)
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'fb direct scene-target read',
                  'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.fbdirect-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def apply_all(main, revert=False, log=print):
    m = nxmap.Main(str(main))
    patches, notes, problems = plan(m, revert=revert)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  refusing to change the resample source.')
        return 1
    log('  resample source (%s=%s, stock reads the capture surface):'
        % (DIRECT_ENV, '0' if revert else ('1' if enabled() else '0')))
    for n in notes:
        log(n)
    if not patches:
        log('    already %s' % read_state(m.img))
        return 0
    _write(main, patches, log)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('main')
    ap.add_argument('--revert', action='store_true')
    ap.add_argument('--state', action='store_true')
    a = ap.parse_args(argv)
    if a.state:
        m = nxmap.Main(a.main)
        print(read_state(m.img))
        for p in verify(m.img):
            print('  ! ' + p)
        return 0
    return apply_all(a.main, revert=a.revert)


if __name__ == '__main__':
    sys.exit(main())
