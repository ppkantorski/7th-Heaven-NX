#!/usr/bin/env python3
r"""
ff7nx_fbpath.py -- put KUJATA'S capture on the port's OTHER capture path.

THE PORT HAS TWO FRAMEBUFFER-CAPTURE IMPLEMENTATIONS
====================================================
`make_framebuffer_tex` (+0x10DBB50) stores its FIRST argument at tex+0x24:

    +0x10DBC98   str  w20, [x26, #0x24]        w20 = w0 = field_0

and the loader (+0x10D7050) branches on exactly that word:

    +0x10D70AC   ldr  w8, [x20, #0x24]
    +0x10D70B0   cmp  w8, #1
    +0x10D70B4   b.ne #0x10D7240               <-- the other path

PATH A -- field_0 == 1 -- CPU readback.  This is the one Kujata takes.
    +0x10D70B8 .. +0x10D7238.  Queries the current surface's width, height and
    stride from its vtable (+0x28 / +0x40 / +0x30), maps it to CPU memory
    (+0x50), and runs a 1:1 pixel copy of a RECTANGLE:

        columns = min(surface_w, x + w) - x
        rows    = min(surface_h, y + h) - y

    No filtering, no scaling, no fit.  It is a CROP out of the 640x480 staging
    surface (+0x10D5970; 320x240 in low-res at +0x10D5358 -- the only two
    surface sizes the binary creates).  Everything past the surface is never
    written, which is the black, and the crop cannot grow to cover more of the
    frame however many texels the texture has.

PATH B -- field_0 != 1 -- GPU render-to-texture.
    +0x10D7240 .. +0x10D74xx.  Creates a render target of exactly
    (fb_tex.w, fb_tex.h), sets the viewport AND the scissor to the WHOLE
    target, binds the staging surface as a texture source and draws it in:

        +0x10D7264   ldp  w2, w3, [x20, #0x1c]     (fb_tex.w, fb_tex.h)
        +0x10D727C   bl   #0x4940                  create target of that size
        +0x10D72F4   ldur x22, [x20, #0x1c]        the packed (w, h)
        +0x10D7304   bl   #0x11320E0               viewport = 0 .. (w, h)
        +0x10D7314   bl   #0x11320F0               scissor  = 0 .. (w, h)
        +0x10D7404   ldr  x25, [.. #0x620]         the staging surface index
        +0x10D7428   bl   #0x1132080               bound as the source

    A filtered, scaled blit of the whole staging surface into the texture.
    Full coverage at any texture size, nothing unwritten, nothing cropped.

WHAT THIS MODULE DOES
=====================
Hooks the selector at +0x10D70B0 and re-tests, from the tex header alone, the
five conditions that identify Kujata's capture and nothing else.  On a match it
branches straight to Path B; on anything else it performs the original
`cmp w8, #1` and returns, so the flags the following `b.ne` reads are exactly
the stock ones and the other twenty-four captures are untouched.

    fb_tex.w == tex_format.width << k      double-scaled by ff7nx_fxcapscale
    fb_tex.h == tex_format.width << k      and square
    tex_format.width == 256                authored 256x256 (the swirl is 160)
    fb_tex.x == 0                          taken at the frame origin
    fb_tex.y == 0

THE GATE ONLY DISCRIMINATES AT CAPSCALE >= 2
============================================
At CAPSCALE 1 the first and third conditions collapse (`fb_tex.w ==
tex_format.width` is true of every 1:1 capture) AND `ff7nx_fbcapture` moves
Kujata's origin to staging column 80, so `fb_tex.x == 0` is false and the gate
could never fire anyway.  `enabled()` therefore returns False unless
`ff7nx_fxcapscale` is at 2 or 4.  The two are one change.

REGISTERS
=========
At the hook, w8 holds `[x20+0x24]` and must survive to the re-issued `cmp`.
x20 is the tex header (set at +0x10D709C).  The cave borrows w15/w16 only --
both dead across the `blr x8` at +0x10D70A8 immediately above.
"""
from __future__ import annotations

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

import a64 as A                                                # noqa: E402
import ff7nx_cave                                              # noqa: E402
import nxmap                                                   # noqa: E402

PATH_ENV = 'SEVENTH_NX_FB_PATH'

HOOK = 0x10D70B0                   # cmp w8, #1
HOOK_STOCK = 0x7100051F
RETURN_VA = 0x10D70B4              # the stock b.ne
GPU_PATH = 0x10D7240               # Path B's first instruction

# ---------------------------------------------------------------- the source
# BUILD 286. Path B does NOT sample the whole surface. Its source UVs are the
# capture rect normalised by the SURFACE's own size (+0x10D7478..+0x10D7508):
#
#     u0 = x / surface_w        u1 = (x + w) / surface_w
#     v0 = y / surface_h        v1 = (y + h) / surface_h
#
# With the rect at 512x512 those run past 1.0 on any surface smaller than
# 512 -- everything past the edge samples the border, which is the black, and
# the axis that overruns least comes back as a slight stretch. Forcing the rect
# to the surface's own width and height makes the UVs exactly 0..1: the WHOLE
# frame, scaled into the texture, which is what the effect wants.
#
# The two registers are already loaded at the hook: w22 = surface width
# (+0x10D7464, from vtable +0x28) and w0 = surface height (+0x10D7474, from
# vtable +0x40). Nothing has to be queried, and nothing has to be assumed about
# what size the surface is at that moment.
UV_HOOK = 0x10D7478                # ldp w8, w9, [x20, #0x14]   x, y
UV_HOOK_STOCK = 0x2942A688
UV_LDP_WH = 0x2943AE8A             # ldp w10, w11, [x20, #0x1c] w, h
UV_RETURN = 0x10D7480              # adrp x20, ...  -- x20 dies here
SURF_W, SURF_H = 22, 0

FB_W_OFF, FB_H_OFF = 0x1C, 0x20
FB_X_OFF, FB_Y_OFF = 0x14, 0x18
TEX_W_OFF = 0x3C
KUJATA_TEX = 0x100

TEX = 20                           # x20 -- the tex header
SCRATCH_A, SCRATCH_B = 15, 16
COND_NE = 1

N_WORDS = 16                       # the selector gate
UV_WORDS = 21                      # the source-rect gate

# BUILD 287: OFF, and it stays off until the gate identifies Kujata's capture
# by something other than its SHAPE. The battle-entry swirl's own xscale is 2,
# so at CAPSCALE 2 the swirl satisfies all five conditions as well -- which is
# what build 286 broke. `enabled()` also requires CAPSCALE >= 2, which is now
# back to 1, so this is off twice over.
DEFAULT_ON = False

# The selector and both path entries, asserted so a game update cannot move
# the branch this module rewrites.
ANCHORS = {
    0x10D709C: 0xAA0103F4,         # mov  x20, x1              the tex header
    0x10D70AC: 0xB9402688,         # ldr  w8, [x20, #0x24]     field_0
    # NOTE: +0x10D70B0 itself is the hook and is NOT an anchor -- `installed()`
    # is what checks it, because after an install it carries the branch.
    0x10D70B4: 0x54000C61,         # b.ne #0x10D7240
    0x10D70B8: 0x2943A688,         # ldp  w8, w9, [x20, #0x1c] Path A begins
    0x10D7240: 0xF0000FB7,         # adrp x23, #0x12CE000      Path B begins
    0x10D7264: 0x29438E82,         # ldp  w2, w3, [x20, #0x1c] target size
    0x10D72F4: 0xF841C296,         # ldur x22, [x20, #0x1c]    viewport size
    0x10D7464: 0x2A0003F6,         # mov  w22, w0              surface WIDTH
    0x10D7474: 0xD63F0100,         # blr  x8   (vtable +0x40)  -> w0 = HEIGHT
    # +0x10D7478 is the second hook, checked by `installed_uv()`
    0x10D747C: 0x2943AE8A,         # ldp  w10, w11, [x20, #0x1c]
    0x10D7480: 0xF0000FB4,         # adrp x20, ...   x20 dies here
    0x10D7484: 0x1E220001,         # scvtf s1, w0              / surface height
    0x10D74A0: 0x1E2202C0,         # scvtf s0, w22             / surface width
}


def cmp_reg_lsl(rn, rm, sh):
    """CMP Wn, Wm, LSL #sh."""
    return 0x6B000000 | (sh << 10) | (rm << 16) | (rn << 5) | A.WZR


def size_shift():
    """log2 of Kujata's capture scale, from ff7nx_fxcapscale."""
    try:
        import ff7nx_fxcapscale
        return {1: 0, 2: 1, 4: 2}[ff7nx_fxcapscale.scale()]
    except Exception:                                          # noqa: BLE001
        return 0


def _gate(addr, k, no):
    """The thirteen words that decide whether this capture is Kujata's.

    Falls through when it is; branches to word `no` when it is not. Borrows
    w15/w16 only, and reads nothing but the tex header in x20.
    """
    return [
        A.ldr(SCRATCH_A, TEX, FB_W_OFF),                        # 0
        A.ldr(SCRATCH_B, TEX, TEX_W_OFF),                       # 1
        cmp_reg_lsl(SCRATCH_A, SCRATCH_B, k),                   # 2
        A.bcond(addr(3), addr(no), COND_NE),                    # 3
        A.cmp_imm(SCRATCH_B, KUJATA_TEX),                       # 4
        A.bcond(addr(5), addr(no), COND_NE),                    # 5
        A.ldr(SCRATCH_A, TEX, FB_H_OFF),                        # 6
        cmp_reg_lsl(SCRATCH_A, SCRATCH_B, k),                   # 7
        A.bcond(addr(8), addr(no), COND_NE),                    # 8
        A.ldr(SCRATCH_A, TEX, FB_X_OFF),                        # 9
        A.cbnz(SCRATCH_A, addr(10), addr(no)),                  # 10
        A.ldr(SCRATCH_A, TEX, FB_Y_OFF),                        # 11
        A.cbnz(SCRATCH_A, addr(12), addr(no)),                  # 12
    ]


def uv_words(addr, k=None):
    """Force Path B's source rect to the surface's own full extent.

    On a match: x = y = 0, w = surface_w, h = surface_h, so the UVs the code
    below computes are exactly 0..1 on both axes -- the whole frame, with
    nothing sampled past the edge. On anything else: the two stock `ldp`s, in
    order, exactly as they stand in the binary.
    """
    k = size_shift() if k is None else k
    no = 18
    return _gate(addr, k, no) + [
        A.mov_reg(8, A.WZR),                                    # 13  x = 0
        A.mov_reg(9, A.WZR),                                    # 14  y = 0
        A.mov_reg(10, SURF_W),                                  # 15  w = surf_w
        A.mov_reg(11, SURF_H),                                  # 16  h = surf_h
        A.b(addr(17), UV_RETURN),                               # 17
        UV_HOOK_STOCK,                                          # 18  ldp x, y
        UV_LDP_WH,                                              # 19  ldp w, h
        A.b(addr(20), UV_RETURN),                               # 20
    ]


def body_words(addr, k=None):
    """The gate. `addr(i)` is the address the i-th word will live at."""
    k = size_shift() if k is None else k
    no = 14                                    # the "not Kujata" continuation
    return [
        A.ldr(SCRATCH_A, TEX, FB_W_OFF),                        # 0
        A.ldr(SCRATCH_B, TEX, TEX_W_OFF),                       # 1
        cmp_reg_lsl(SCRATCH_A, SCRATCH_B, k),                   # 2
        A.bcond(addr(3), addr(no), COND_NE),                    # 3
        A.cmp_imm(SCRATCH_B, KUJATA_TEX),                       # 4
        A.bcond(addr(5), addr(no), COND_NE),                    # 5
        A.ldr(SCRATCH_A, TEX, FB_H_OFF),                        # 6
        cmp_reg_lsl(SCRATCH_A, SCRATCH_B, k),                   # 7
        A.bcond(addr(8), addr(no), COND_NE),                    # 8
        A.ldr(SCRATCH_A, TEX, FB_X_OFF),                        # 9
        A.cbnz(SCRATCH_A, addr(10), addr(no)),                  # 10
        A.ldr(SCRATCH_A, TEX, FB_Y_OFF),                        # 11
        A.cbnz(SCRATCH_A, addr(12), addr(no)),                  # 12
        A.b(addr(13), GPU_PATH),                                # 13  -> Path B
        HOOK_STOCK,                                             # 14  cmp w8,#1
        A.b(addr(15), RETURN_VA),                               # 15
    ]


def on() -> bool:
    v = os.environ.get(PATH_ENV)
    if v is None:
        return DEFAULT_ON
    return v.strip().lower() not in ('', '0', 'off', 'false', 'no')


def enabled() -> bool:
    """Never on its own: the gate cannot identify Kujata below CAPSCALE 2."""
    return on() and size_shift() > 0


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def _b_target(word, va):
    if (word & 0xFC000000) != 0x14000000:
        return None
    off = word & 0x03FFFFFF
    if off & 0x02000000:
        off -= 0x04000000
    return va + off * 4


# Each site: (hook, stock word, word count, body builder, exit addresses).
SITES = (
    ('selector', HOOK, HOOK_STOCK, N_WORDS,
     lambda addr, k: body_words(addr, k=k), (RETURN_VA, GPU_PATH)),
    ('source rect', UV_HOOK, UV_HOOK_STOCK, UV_WORDS,
     lambda addr, k: uv_words(addr, k=k), (UV_RETURN,)),
)


def _walk_site(img, hook, n, exits):
    """(logical, physical) addresses of one cave, following run-to-run links."""
    entry = _b_target(_word(img, hook), hook)
    if entry is None:
        return [], []
    logical, physical, seen, va = [], [], set(), entry
    while len(logical) < n and va not in seen and 0 <= va <= len(img) - 4:
        seen.add(va)
        physical.append(va)
        tgt = _b_target(_word(img, va), va)
        if tgt is not None and tgt not in exits:
            va = tgt                           # a run-to-run link, never logic
            continue
        logical.append(va)
        va += 4
    return logical, physical


def _walk(img):
    """Back-compat: the selector cave."""
    return _walk_site(img, HOOK, N_WORDS, (RETURN_VA, GPU_PATH))


def _installed_site(img, hook, stock, n, build, exits):
    if _word(img, hook) == stock:
        return -1
    addrs, _ = _walk_site(img, hook, n, exits)
    if len(addrs) != n:
        return None
    got = [_word(img, a) for a in addrs]
    for k in (0, 1, 2):
        if got == build(lambda i: addrs[i], k):
            return k
    return None


def installed(img):
    """The shift BOTH caves carry, or None, or -1 when both are stock."""
    found = {_installed_site(img, *s[1:]) for s in SITES}
    if None in found or len(found) != 1:
        return None
    return found.pop()


def verify(img) -> list:
    bad = []
    for va, want in sorted(ANCHORS.items()):
        got = _word(img, va)
        if got != want:
            bad.append('anchor +0x%X is %08X, expected %08X' % (va, got, want))
    for name, hook, stock, n, build, exits in SITES:
        if _installed_site(img, hook, stock, n, build, exits) is None:
            bad.append('+0x%X (%s) carries neither its stock instruction nor a '
                       'gate this build wrote' % (hook, name))
    if installed(img) is None and not bad:
        bad.append('the two gates disagree about the capture scale')
    return bad


def read_state(img) -> str:
    k = installed(img)
    if k is None:
        return 'unknown'
    if k == -1:
        return 'capture path: stock (CPU readback for every capture)'
    return 'capture path: Kujata -> GPU render-to-texture (gate shift %d)' % k


def plan(m, revert=False):
    import cave_space
    img = m.img
    problems = verify(img)
    if problems:
        return [], [], problems
    want = -1 if (revert or not enabled()) else size_shift()
    have = installed(img)
    if have == want:
        return [], [], []
    if have != -1 and want != -1:
        return [], [], ['RESIZE']

    patches, notes = [], []
    if have != -1:
        for name, hook, stock, n, build, exits in SITES:
            for va in _walk_site(img, hook, n, exits)[1]:
                patches.append({'name': 'clear the %s gate' % name,
                                'va': hex(va),
                                'expect': struct.pack('<I',
                                                      _word(img, va)).hex(),
                                'set': '00000000'})
            patches.append({'name': 'restore the stock %s' % name,
                            'va': hex(hook),
                            'expect': struct.pack('<I', _word(img, hook)).hex(),
                            'set': struct.pack('<I', stock).hex()})
    if want == -1:
        return patches, ['    capture path back to stock'], []

    pool = ff7nx_cave.HolePool(
        img, starts=set(m.arm_starts),
        named=cave_space.named_targets(img[cave_space.RODATA:]))
    for name, hook, stock, n, build, exits in SITES:
        try:
            runs = pool.take(n, span=0x80000)
            slots = ff7nx_cave.slots(runs, n)
            placed = ff7nx_cave.link(runs, build(lambda i: slots[i], want))
        except ff7nx_cave.NoRoom as exc:
            return [], [], ['capture-path %s cave: %s' % (name, exc)]
        placed[hook] = A.b(hook, slots[0])
        for va, wd in sorted(placed.items()):
            patches.append({'name': 'Kujata GPU capture: %s' % name,
                            'va': hex(va),
                            'expect': struct.pack('<I', _word(img, va)).hex(),
                            'set': struct.pack('<I', wd).hex()})
        notes.append('    %-12s gate shift %d, cave entry +0x%X'
                     % (name, want, slots[0]))
    notes.insert(0, '    Kujata routed to the GPU path (+0x%X), and its source '
                    'rect forced to the surface\'s own full extent' % GPU_PATH)
    return patches, notes, []


def _write(main, patches, log):
    import nso_patcher
    main = Path(main)
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'Kujata capture path', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.fbpath-')
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
    if problems == ['RESIZE']:
        log('  capture path: re-gating, reverting first')
        if apply_all(main, revert=True, log=log) != 0:
            return 1
        m = nxmap.Main(str(main))
        patches, notes, problems = plan(m, revert=False)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  refusing to change the capture path.')
        return 1
    log('  Kujata capture path (%s=%s; needs SEVENTH_NX_FX_CAPSCALE >= 2; NO '
        'other capture in the game is touched):'
        % (PATH_ENV, 'off' if (revert or not enabled()) else 'on'))
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    _write(main, patches, log)
    log('  %d capture-path word(s) written' % len(patches))
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('main')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args()
    raise SystemExit(apply_all(a.main, revert=a.revert))
