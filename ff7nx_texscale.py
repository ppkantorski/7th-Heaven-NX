#!/usr/bin/env python3
"""
ff7nx_texscale.py -- cap the port's filtered texture copy at 1024 px (BUILD 603).

THE FAULT (hardware, leakprobe v2, logs/crash_lp2_01790601486.log)
GL_OUT_OF_MEMORY in fship and nowhere else: 3 on the first entry, 8 on the
second. The graphics pool (256 MB, nv::InitializeGraphics) runs out while
fship loads, and a texture that gets no memory is drawn from garbage -- the
crew's and Cloud's speckles. The port checks GL errors only after its 13
framebuffer calls, never after a texture upload, so it was silent. The world
map's churn fragments the pool, which is why it grew with every trip,
survived a quit to the title, and was worse after flying high.

WHERE THE POOL GOES
Every texture the loader uploads (+0x10DCA90 -> +0x4620) also gets a filtered
copy for the background scaler (HQ4x, or the build's HD scaler), created at
4x the width AND the height: 17x the texture's memory. The scale is a literal
`mov w5, #4` at two sites:

    +0x10DCB34   gfx_drv texture upload (w21 x w20)  -> +0x4620
    +0x10D7270   framebuffer-copy texture (w2 x w3)   -> +0x4940

fship's mod-sized 512x512 textures each carried a 2048x2048 copy, 16 MB.

THE FIX
Each `mov w5, #4` becomes `bl` to a branch-free 10-word cave in verified
padding: scale 4 up to 256 px on the longer side, 2 up to 512, 1 above -- the
copy never exceeds 1024 px a side. Every stock-size texture (<= 256) is
unchanged; a 512x512 copy goes 16 MB -> 4 MB. The filter pass (+0x10DBCC0)
takes its viewport from the copy's real size (vtable 40/64 of the target)
and passes the source size as a uniform, so a smaller copy renders
correctly, and the scalers resample to any size.

Both sites sit in functions that saved x30 in their prologue, and the next
instructions set w1..w4 and call without reading the flags, so the cave may
clobber w16, w17 and NZCV. CONFIRMED ON HARDWARE (build 603 test via
texscale.py): the speckles are gone.

SEVENTH_NX_TEXSCALE=0 leaves both sites stock.
"""
import os
import struct

import a64 as A
import ff7nx_cave

ENV = 'SEVENTH_NX_TEXSCALE'
STOCK_WORD = 0x321E03E5                      # orr w5, wzr, #0x4 (mov w5, #4)
CAP = 1024

# (site, the two words that pick the longer side, what)
SITES = (
    (0x10DCB34, (0x6B1402BF, 0x1A9482B0),     # cmp w21, w20 ; csel w16, w21, w20, hi
     'texture upload (w21 x w20)'),
    (0x10D7270, (0x6B03005F, 0x1A838050),     # cmp w2, w3 ; csel w16, w2, w3, hi
     'framebuffer-copy texture (w2 x w3)'),
)
# w5 = 4; if w16 > 256: 2; if w16 > 512: 1
TAIL = (0x52800085,                          # mov  w5, #4
        0x7104021F,                          # cmp  w16, #256
        0x52800051,                          # mov  w17, #2
        0x1A9190A5,                          # csel w5, w5, w17, ls
        0x7108021F,                          # cmp  w16, #512
        0x52800031,                          # mov  w17, #1
        0x1A9190A5,                          # csel w5, w5, w17, ls
        0xD65F03C0)                          # ret


def enabled(env=None):
    env = os.environ if env is None else env
    return env.get(ENV, '1').strip().lower() not in ('0', 'off', 'no', 'false')


def scale_of(w, h):
    m = max(w, h)
    return 4 if m <= 256 else 2 if m <= 512 else 1


def body(pick):
    return list(pick) + list(TAIL)


def hx(word):
    """nso_patcher's byte string: little-endian, space separated."""
    return ' '.join('%02X' % b for b in struct.pack('<I', word))


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def _bl_target(va, w):
    if (w >> 26) != 0x25:
        return None
    imm = w & 0x3FFFFFF
    if imm & 0x2000000:
        imm -= 0x4000000
    return va + 4 * imm


def _walk(img, va, n):
    """The n words of a cave laid out by ff7nx_cave (runs joined by `b`)."""
    out = []
    guard = 0
    while len(out) < n and guard < 4 * n:
        guard += 1
        w = _word(img, va)
        if (w >> 26) == 0x05:            # b next run (the body has no `b`)
            imm = w & 0x3FFFFFF
            if imm & 0x2000000:
                imm -= 0x4000000
            va = va + 4 * imm
            continue
        out.append(w)
        va += 4
    return out


def site_state(img, site, pick):
    w = _word(img, site)
    if w == STOCK_WORD:
        return 'stock'
    tgt = _bl_target(site, w)
    if tgt is not None and _walk(img, tgt, len(body(pick))) == body(pick):
        return 'capped'
    return 'unknown'


def read_state(img):
    states = {site_state(img, s, p) for s, p, _w in SITES}
    return states.pop() if len(states) == 1 else 'mixed'


def build_patches(img, starts=None, log=lambda *_: None):
    pool = ff7nx_cave.HolePool(bytearray(img), starts=starts)
    words = {}
    for site, pick, what in SITES:
        if _word(img, site) != STOCK_WORD:
            raise ValueError('%s at +0x%X is %08X, expected %08X (mov w5, #4)'
                             % (what, site, _word(img, site), STOCK_WORD))
        entry, cave = ff7nx_cave.emit_laid_out(
            pool, lambda _e, _addr, p=pick: body(p))
        words.update(cave)
        words[site] = A.bl(site, entry)
        log('  texscale cave for %s: %d words in verified padding, entry +0x%X'
            % (what, len(body(pick)), entry))
    return words


def apply_to_nso(src, dest, log=lambda *_: None):
    """Write `dest` from `src`. False when nothing was written."""
    from pathlib import Path as _P
    import nxmap
    import nso_patcher
    module = nxmap.Main(str(src))
    state = read_state(module.img)
    if state == 'capped':
        log('  texscale: already capped; nothing to write')
        return False
    if state != 'stock':
        log('! texscale: the two sites are %s -- neither stock nor this '
            'build\'s caves; nothing was written' % state)
        return False
    try:
        words = build_patches(module.img, set(module.arm_starts), log)
    except Exception as exc:                                  # noqa: BLE001
        log('! texscale: %s: %s' % (type(exc).__name__, exc))
        log('  nothing was written; the module is unchanged')
        return False
    spec = {'name': 'texscale: filtered copy <= %d px' % CAP,
            'patches': [{'name': '+0x%X' % va, 'va': hex(va),
                         'expect': hx(_word(module.img, va)),
                         'set': hx(words[va])} for va in sorted(words)]}
    nso = nso_patcher.read_nso(_P(str(src)))
    nso_patcher.apply_spec(nso, spec)
    data = nso_patcher.rebuild(nso)
    os.makedirs(os.path.dirname(os.path.abspath(str(dest))), exist_ok=True)
    with open(str(dest), 'wb') as f:
        f.write(data)
    log('  texscale: the scaler\'s filtered copy is 4x up to 256 px, 2x up to '
        '512, 1x above (never over %d px a side; a 512x512 copy 16 MB -> 4 MB);'
        ' stock-size art unchanged' % CAP)
    return True
