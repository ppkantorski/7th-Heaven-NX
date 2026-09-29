#!/usr/bin/env python3
r"""
texscale.py -- cap the port's 4x filtered texture copy at 1024 px. On a
BUILT main, no rebuild. THE FSHIP SPECKLE FIX UNDER TEST (BUILD 603).

    python3 texscale.py sdout/atmosphere/contents/0100A5B00BDC6000/exefs/main --on
    python3 texscale.py <same main> --off
    python3 texscale.py <same main> --show

WHAT IS WRONG (leakprobe v2, logs/crash_lp2_01790601486.log)
GL_OUT_OF_MEMORY, in fship and nowhere else: 3 on the first entry, 8 on the
second, none in any other visit. The graphics pool (256 MB) runs out while
fship loads; a texture that gets no memory is drawn from garbage. Nothing in
the port reads that error after a texture upload, so it was silent.

WHERE THE POOL GOES
Every texture the port uploads (+0x10DCA90 -> +0x4620) also gets a second,
filtered copy for the background scaler (HQ4x / your "HD Catmull-Rom +
sharpen"), created at 4x the width AND height -- 17x the memory of the
texture. fship loads mod-sized 512x512 textures: each one's copy is 2048x2048,
16 MB. The world map's churn fragments the pool, and after enough trips those
16 MB allocations stop fitting.

THE FIX
The scale is the literal `mov w5, #4` at +0x10DCB34 (loader textures) and
+0x10D7270 (framebuffer-copy textures). Both become a call to a 13-word
routine: scale 4 up to 256 px on the longer side, 2 up to 512, 1 above --
the copy is never bigger than 1024 px on a side. 256 px and smaller (all the
stock art) is unchanged. The filter pass (+0x10DBCC0) takes its viewport from
the copy's real size and the source size as a uniform, so a smaller copy
renders correctly, and the HD scaler resamples to any size.

The routine goes at the top of the dead-space 'probe' part (leakprobe and
texprobe use the bottom). Every site is checked before anything is written.
"""
import os
import shutil
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import a64 as A                              # noqa: E402
import ff7nx_audio_cave as AC                # noqa: E402
import ff7nx_deadspace as DS                 # noqa: E402

STOCK_WORD = 0x321E03E5                      # orr w5, wzr, #0x4  (mov w5, #4)
SITES = ((0x10DCB34, 'wh20_21', 'loader texture upload (w21 x w20)'),
         (0x10D7270, 'w2_h3', 'framebuffer-copy texture (w2 x w3)'))

# scale_wh20_21 at +0, scale_w2_h3 at +0xC (assembled from the source below
# with GNU as; the words are checked by tests in texscale_check()).
#   scale_wh20_21: cmp w21, w20 ; csel w16, w21, w20, hi ; b pick
#   scale_w2_h3:   cmp w2, w3   ; csel w16, w2, w3, hi
#   pick: mov w5, #4 ; cmp w16, #256 ; b.ls 1f ; mov w5, #2 ; cmp w16, #512
#         b.ls 1f ; mov w5, #1 ; 1: ret
CODE = (0x6B1402BF, 0x1A9482B0, 0x14000003,
        0x6B03005F, 0x1A838050,
        0x52800085, 0x7104021F, 0x540000A9, 0x52800045, 0x7108021F,
        0x54000049, 0x52800025, 0xD65F03C0)
ENTRY = {'wh20_21': 0x0, 'w2_h3': 0xC}
ROOM = 0x100                                 # at probe-part top - ROOM


def scale_of(w, h):
    """What the routine computes (for the tests and the log)."""
    m = max(w, h)
    return 4 if m <= 256 else 2 if m <= 512 else 1


def place(text, src):
    _lo, hi = DS.part(src, 'probe')
    return ((hi & ~15) - ROOM) & ~15


def apply(src, dest):
    blob = open(src, 'rb').read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    for va, _k, what in SITES:
        got = struct.unpack_from('<I', text, va)[0]
        if got != STOCK_WORD:
            raise ValueError('%s at +0x%X is %08X, expected %08X (mov w5, #4) '
                             '-- already on?' % (what, va, got, STOCK_WORD))
    at = place(text, src)
    for i, w in enumerate(CODE):
        struct.pack_into('<I', text, at + 4 * i, w)
    for va, key, _what in SITES:
        struct.pack_into('<I', text, va, A.bl(va, at + ENTRY[key]))
    raw[0] = bytes(text)
    open(dest, 'wb').write(AC.pack(blob, raw, 0))
    return at


def is_on(path):
    t = AC.segments(open(path, 'rb').read())[1][0]
    return all(struct.unpack_from('<I', t, va)[0] != STOCK_WORD
               for va, _k, _w in SITES)


def main(argv):
    if len(argv) < 3 or argv[2] not in ('--on', '--off', '--show'):
        print(__doc__)
        return 2
    path = argv[1]
    backup = path + '.pre-texscale'
    if argv[2] == '--show':
        print('  texscale is %s' % ('ON (filtered copies capped at 1024 px)'
                                    if is_on(path) else 'off (stock 4x)'))
        return 0
    if argv[2] == '--off':
        if not os.path.exists(backup):
            print('  no %s -- rebuild instead' % os.path.basename(backup))
            return 1
        shutil.copyfile(backup, path)
        os.remove(backup)
        print('  texscale -> off (the main from before --on is back)')
        return 0
    if os.path.exists(backup):
        print('  already on (%s exists); --off first' % os.path.basename(backup))
        return 1
    shutil.copyfile(path, backup)
    try:
        at = apply(backup, path)
    except Exception:
        shutil.copyfile(backup, path)
        os.remove(backup)
        raise
    print('  texscale -> on: routine at +0x%X; filtered copy 4x up to 256 px, '
          '2x up to 512, 1x above (512x512: 16 MB -> 4 MB)' % at)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
