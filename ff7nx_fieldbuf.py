#!/usr/bin/env python3
"""
ff7nx_fieldbuf.py -- resize the low-resolution field render target.

THE BANDS, EXPLAINED
====================
The port does not draw the field straight into the screen-sized render
target. `gfx_drv_init` (+0x10D5358) creates **eight 320x240 render targets**
from a single `mov x8, #0x140 ; movk x8, #0xf0, lsl #32` pair, and the
driver's render-mode switch (+0x10DF6E0) points the viewport transform at
them for modes 0, 2 and 3 -- the field modes -- by writing 320 and 240 into
the "current target size" globals `[[0x12CE578]]` / `[[0x12CE580]]`.

`gfx_drv_setviewport` (+0x10D6760) then maps game space onto that size with
`px = target_w * x / 640`, so with target_w = 320 the whole 640-unit game
space lands on **320 pixels**. That is not a bug -- it is the point. A field
background tile is 16x16 source texels and covers 32x32 game units, so at
320 px it lands **exactly 1:1, one texel per pixel**. The 2xSaI / HQ4x
"background scaler" then upscales that buffer to the screen. That is the
whole reason those shaders exist.

Widescreen broke the 1:1. `WS_SCALE 0.75` packs 853.33 game units into the
same 320 pixels, so a 16-texel tile is rasterised into **12 pixels**. 16
texels into 12 pixels is a minification whose sampling phase repeats every
3 buffer pixels, and the fixed upscale to the screen magnifies those 3
pixels into 12 SCREEN PX at 720p -- the bands. The beat is locked to the
buffer's pixel grid, which is locked to the screen, so the bands sit still
while the art slides through them.

Confirmed on hardware, 2026-08: widening the buffer to 428 and setting
`WS_SCALE = 320/428` removes them.

THE GENERAL RULE
================
With buffer `W x H` and shader scale `S`, the field gets

    W * S / 640   buffer px per game unit   (horizontal)
    H / 480       buffer px per game unit   (vertical)

A background texel is half a game unit, so `n` **whole** pixels per texel --
which is what "no beat" means -- requires

    W * S / 640  ==  H / 480  ==  n / 2,        n = 1, 2, 3, ...

    =>   H = 240n,   S = 320n / W,   visible span = 640/S = 2W/n units

so `W` alone picks the scale AND the aspect ratio (`2W / 480n`), and `n` is
a free supersample factor for the whole field pass.

`W` must be chosen so `W/2 * (1 - S) = (W - 320n)/2` is an integer, or game
x = 0 lands on a half pixel and every tile edge is blended across two of
them -- no bands, but a uniform half-texel blur.

    n   W      H     S            span     aspect vs 16:9
    1   428    240   0.74766355   856      +0.31%
    2   854    480   0.74941452   854      +0.08%
    3   1280   720   0.75         853.33   EXACT

**n = 3 is the arithmetically perfect one**: 1280/720 is exactly 16:9, the
span is exactly 853.33 units, `S` stays exactly 0.75, and at 720p handheld
the field is finally rendered at native screen resolution instead of being
reconstructed from a 320-wide buffer. It also costs 9x the field fill rate
and magnifies the pre-rendered background 3x with the hardware sampler
instead of letting the 2xSaI/HQ4x kernel reconstruct it, so it is a
different LOOK, not simply a better one. n = 1 is what hardware has
confirmed; n = 2 and n = 3 are worth a build each.

With widescreen OFF the same rule gives `W = 320n, H = 240n, S = 1.0` --
a pure supersample of the field pass at stock 4:3 framing.

THE PATCH
=========
Eight words, all `movz`/`movk`/`orr` immediates in the native ARM64 driver
shim. No caves, no displaced instructions, idempotent, byte-exactly
reversible, every site verified against a multi-word signature first.

    python3 ff7nx_fieldbuf.py <main> --show
    python3 ff7nx_fieldbuf.py <main> --scale 1        # 428x240, ships
    python3 ff7nx_fieldbuf.py <main> --scale 3        # 1280x720, exact 16:9
    python3 ff7nx_fieldbuf.py <main> --scale 1 --four-three
    python3 ff7nx_fieldbuf.py <main> --size 428x240
    python3 ff7nx_fieldbuf.py <main> --stock          # back to 320x240
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
from pathlib import Path

# ONLY this directory. The parent used to be on this list and it is a trap:
# it puts any superseded loose copy sitting beside the project folder ahead
# of the real module, and the shadowing CASCADES -- a stale module imported
# from the parent then inserts ITS own directory, so one wrong entry drags in
# every other stale file next to it. That is what broke `import build` here
# (a 2026-08-08 ff7nx_uiclip with no UICLIP_ENV). Everything this module
# needs -- a64, nxmap, nso_patcher -- lives beside it. Same note as
# ff7nx_heap and ff7nx_glerror.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

STOCK_WIDTH = 320
STOCK_HEIGHT = 240

# Selects a preset from the environment. '0'/'' = leave the buffer stock.
SCALE_ENV = 'SEVENTH_NX_WS_FIELDBUF'

# The default when the 16:9 framing stage runs. 1 is the value hardware has
# confirmed; see the module docstring for why 2 and 3 are not the default
# even though 3 is arithmetically nicer.
DEFAULT_SCALE = 1


# --------------------------------------------------------------------------
# presets
# --------------------------------------------------------------------------
# width per supersample step, for 16:9. The ideal is 426.667n; each entry is
# the nearest EVEN value that also keeps (W - 320n) even, so game x = 0
# lands on a whole buffer pixel.
_WIDE_WIDTH = {1: 428, 2: 854, 3: 1280}


def preset(scale: int, widescreen: bool = True) -> dict | None:
    """
    {'width', 'height', 'scale', 'ws_scale'} for a supersample step, or None
    for `scale` 0 / falsy, which means "leave the buffer alone".
    """
    if not scale:
        return None
    scale = int(scale)
    if scale < 1:
        return None
    height = STOCK_HEIGHT * scale
    if widescreen:
        width = _WIDE_WIDTH.get(scale)
        if width is None:                     # extrapolate, keeping parity
            width = int(round(1280 * scale / 3.0))
            width -= (width - STOCK_WIDTH * scale) % 2
    else:
        width = STOCK_WIDTH * scale
    return {'width': width, 'height': height, 'scale': scale,
            'ws_scale': float(STOCK_WIDTH * scale) / width}


def env_scale(default: int = 0) -> int:
    """The preset the environment asks for. Unparseable values mean 0."""
    raw = (os.environ.get(SCALE_ENV) or '').strip().lower()
    if raw in ('', 'default'):
        return default
    if raw in ('off', 'no', 'false', 'stock', '0'):
        return 0
    try:
        n = int(raw)
    except ValueError:
        return default
    return n if n >= 1 else 0


def ws_scale(width: int = None, scale: int = 1) -> float:
    """
    The `#define WS_SCALE` that keeps texels on whole buffer pixels.
    Called with no width it answers for the shipping preset.
    """
    if width is None:
        p = preset(scale)
        return p['ws_scale'] if p else 1.0
    return float(STOCK_WIDTH * scale) / width


def visible_units(width: int, scale: int = 1) -> float:
    return 2.0 * width / scale


def aspect(width: int, scale: int = 1) -> float:
    return visible_units(width, scale) / 480.0


# --------------------------------------------------------------------------
# the four sites, eight words
# --------------------------------------------------------------------------
# `words` is the STOCK block, read straight out of the module. Every word
# not named in `fields` must match exactly before anything is written --
# `movz w?, #0x140` is a common word (132 of them in .text) and a bare word
# compare would not prove the hook landed on the right instruction. Each
# signature carries the neighbouring height and the store that proves what
# the value is for.
#
# kinds:
#   'movz64'  movz Xd, #imm16                    -- width, packed struct
#   'movk64h' movk Xd, #imm16, lsl #32           -- height, packed struct
#   'movz32'  movz Wd, #imm16                    -- width, mode switch
#   'orr32'   orr Wd, wzr, #imm  (stock 240)     -- height, mode switch;
#             rewritten as movz, which is the same operation and always
#             encodable, where the ORR bitmask form is not (720 is not a
#             logical immediate).
BLOCKS = [
    {
        'name': 'gfx_drv_init: low-res render target size',
        'va': 0x10D5358,
        'words': [
            0xD2802808,   # mov  x8, #0x140            <- width
            0xF2C01E08,   # movk x8, #0xf0, lsl #32    <- height
            0xF90003E8,   # str  x8, [sp]                 the size struct
        ],
        'fields': {0: ('movz64', 8, 'width', STOCK_WIDTH),
                   1: ('movk64h', 8, 'height', STOCK_HEIGHT)},
        'note': 'creates all eight low-res targets',
    },
    {
        'name': 'render-mode 0: current target size',
        'va': 0x10DF760,
        'words': [
            0x5280280D,   # mov  w13, #0x140           <- width
            0x321C0FEF,   # mov  w15, #0xf0            <- height
            0x3900010B,   # strb w11, [x8]
            0x3900012B,   # strb w11, [x9]
            0x3900014B,   # strb w11, [x10]
            0xB900018D,   # str  w13, [x12]               -> [[0x12CE578]]
            0xB90001CF,   # str  w15, [x14]               -> [[0x12CE580]]
        ],
        'fields': {0: ('movz32', 13, 'width', STOCK_WIDTH),
                   1: ('orr32', 15, 'height', STOCK_HEIGHT)},
        'note': 'driver render mode 0',
    },
    {
        'name': 'render-mode 2: current target size',
        'va': 0x10DF7D0,
        'words': [
            0xF0000F68,   # adrp x8,  0x12ce000
            0xF0000F6A,   # adrp x10, 0x12ce000
            0xF942BD08,   # ldr  x8,  [x8,  #0x578]
            0xF942C14A,   # ldr  x10, [x10, #0x580]
            0x52802809,   # mov  w9,  #0x140           <- width
            0x321C0FEB,   # mov  w11, #0xf0            <- height
            0xB9000109,   # str  w9,  [x8]
            0xB900014B,   # str  w11, [x10]
        ],
        'fields': {4: ('movz32', 9, 'width', STOCK_WIDTH),
                   5: ('orr32', 11, 'height', STOCK_HEIGHT)},
        'note': 'driver render mode 2',
    },
    {
        'name': 'render-mode 3: current target size',
        'va': 0x10DF7F4,
        'words': [
            0xF0000F68,   # adrp x8,  0x12ce000
            0xF942BD08,   # ldr  x8,  [x8, #0x578]
            0xF0000F6A,   # adrp x10, 0x12ce000
            0xF942C14A,   # ldr  x10, [x10, #0x580]
            0x52802809,   # mov  w9,  #0x140           <- width
            0xB9000109,   # str  w9,  [x8]
            0x321C0FE8,   # mov  w8,  #0xf0            <- height
            0xB9000148,   # str  w8,  [x10]
        ],
        'fields': {4: ('movz32', 9, 'width', STOCK_WIDTH),
                   6: ('orr32', 8, 'height', STOCK_HEIGHT)},
        'note': 'driver render mode 3',
    },
]

MAX_IMM = 0xFFFF

# gfx_drv_init creates EIGHT targets from the one size struct at +0x10D5358.
# That multiplier is the whole reason the presets have a memory cost worth
# talking about, and it comes out of the same pool the field background
# textures allocate from.
TARGET_COUNT = 8
BYTES_PER_PIXEL = 4


def memory_cost(width: int, height: int) -> float:
    """MB the eight low-res render targets occupy at this size."""
    return (TARGET_COUNT * width * height * BYTES_PER_PIXEL) / (1024.0 ** 2)


def memory_delta(width: int, height: int) -> float:
    """MB more than the stock 320x240 set."""
    return memory_cost(width, height) - memory_cost(STOCK_WIDTH, STOCK_HEIGHT)


# Above this much extra, the field background page budget -- which was
# MEASURED at the stock buffer size -- is no longer measured for the build
# you are running. `field_load_textures` (x86 0x640292) aborts the whole
# loop on the FIRST page it cannot allocate, and every page after it keeps
# handle 0 and never draws, so its tiles show whatever the buffer already
# held. That used to read as black squares; with a bigger buffer holding
# different leftovers it can be any colour.
MEMORY_WARN_MB = 4.0


# --------------------------------------------------------------------------
# encoding
# --------------------------------------------------------------------------
def encode(kind: str, rd: int, imm: int) -> int:
    if not 0 <= imm <= MAX_IMM:
        raise ValueError('%d is not encodable as a 16-bit immediate' % imm)
    if kind == 'movz64':
        return 0xD2800000 | (imm << 5) | rd
    if kind == 'movk64h':
        return 0xF2C00000 | (imm << 5) | rd
    if kind in ('movz32', 'orr32'):
        # ORR-form is only used by the stock words; anything we write is a
        # MOVZ, which encodes every 16-bit value and does the same thing.
        return 0x52800000 | (imm << 5) | rd
    raise ValueError('unknown kind %r' % kind)


def decode(kind: str, rd: int, word: int, stock_word: int,
           stock_value: int) -> int | None:
    """The immediate a word carries, or None if it is not one of ours."""
    if word == stock_word:
        return stock_value
    if kind == 'movz64':
        base = 0xD2800000
    elif kind == 'movk64h':
        base = 0xF2C00000
    else:
        base = 0x52800000
    if (word & 0xFFE0001F) != (base | rd):
        return None
    return (word >> 5) & 0xFFFF


def hx(word: int) -> str:
    return ' '.join('%02X' % b for b in struct.pack('<I', word))


# --------------------------------------------------------------------------
# reading and writing
# --------------------------------------------------------------------------
def _img(main):
    import nxmap
    return nxmap.Main(str(main)).img


def _fields(img):
    """[(va, kind, rd, what, stock_word, stock_value, current_word), ...]"""
    out = []
    for blk in BLOCKS:
        for i, (kind, rd, what, stock_value) in sorted(blk['fields'].items()):
            va = blk['va'] + 4 * i
            out.append((va, kind, rd, what, blk['words'][i], stock_value,
                        struct.unpack_from('<I', img, va)[0]))
    return out


def read_size(main):
    """(width, height) the module is set to, or None if it is undecodable."""
    img = _img(main) if not isinstance(main, (bytes, bytearray)) else main
    seen = {'width': set(), 'height': set()}
    for va, kind, rd, what, sw, sv, cur in _fields(img):
        v = decode(kind, rd, cur, sw, sv)
        if v is None:
            return None
        seen[what].add(v)
    if len(seen['width']) != 1 or len(seen['height']) != 1:
        return None
    return seen['width'].pop(), seen['height'].pop()


def verify_sites(main):
    """Complaints about the module. Empty means it is ours to patch."""
    img = _img(main) if not isinstance(main, (bytes, bytearray)) else main
    bad = []
    for blk in BLOCKS:
        for i, expect in enumerate(blk['words']):
            va = blk['va'] + 4 * i
            have = struct.unpack_from('<I', img, va)[0]
            fld = blk['fields'].get(i)
            if fld is None:
                if have != expect:
                    bad.append('+0x%07X holds %08X, expected %08X -- %s does '
                               'not match this module'
                               % (va, have, expect, blk['name']))
                continue
            kind, rd, what, stock_value = fld
            v = decode(kind, rd, have, expect, stock_value)
            if v is None:
                bad.append('+0x%07X holds %08X, which is not the %s '
                           'instruction this site must be (%s)'
                           % (va, have, kind, blk['name']))
            elif not (16 <= v <= 8192):
                bad.append('+0x%07X holds an implausible %s of %d (%s)'
                           % (va, what, v, blk['name']))
    return bad


def patches(img, width: int, height: int) -> list[dict]:
    """The nso_patcher patch list, or [] if there is nothing to do."""
    want = {'width': width, 'height': height}
    out = []
    for va, kind, rd, what, sw, sv, cur in _fields(img):
        target = want[what]
        # Prefer the module's OWN stock word when the target happens to be
        # the stock value. Two reasons, both learned the hard way: the two
        # height sites are `orr Wd, wzr, #0xf0` and re-encoding them as MOVZ
        # would rewrite four words for no change at scale 1, and going back
        # to 240 from 480 would leave a MOVZ where an ORR used to be -- so
        # "apply then revert" would no longer be byte-exact, which is the
        # property every other patch in this project is held to.
        new = sw if target == sv else encode(kind, rd, target)
        if cur == new:
            continue
        out.append({'name': '%s %d -> %d @ +0x%07X'
                            % (what, decode(kind, rd, cur, sw, sv),
                               target, va),
                    'va': va, 'expect': hx(cur), 'set': hx(new)})
    return out


def spec(img, width: int, height: int) -> dict | None:
    """
    An nso_patcher spec, so a build can fold this into the same transaction
    as the rest of the 16:9 set. None when nothing needs writing.
    """
    ps = patches(img, width, height)
    if not ps:
        return None
    return {'name': 'field render target %dx%d' % (width, height),
            'patches': ps}


def check(width: int, height: int, log=print) -> bool:
    """Refuse sizes that would look wrong, with the reason."""
    ok = True
    if not (STOCK_WIDTH <= width <= MAX_IMM
            and STOCK_HEIGHT <= height <= MAX_IMM):
        log('! %dx%d is out of range' % (width, height))
        return False
    if height % STOCK_HEIGHT:
        log('! height %d is not a multiple of 240, so the vertical scale is '
            'no longer a whole number of pixels per texel and the bands come '
            'back sideways' % height)
        ok = False
    n = height // STOCK_HEIGHT
    if (width - STOCK_WIDTH * n) % 2:
        log('! width %d puts game x = 0 on a half buffer pixel (%.1f). Every '
            'tile edge would be blended across two pixels -- no bands, but a '
            'uniform half-texel blur. Use %d or %d.'
            % (width, (width - STOCK_WIDTH * n) / 2.0, width - 1, width + 1))
        ok = False
    return ok


def apply(main, width: int, height: int, dry_run: bool = False,
          log=print) -> int:
    import nso_patcher
    main = str(main)
    if not check(width, height, log):
        return 2
    bad = verify_sites(main)
    if bad:
        log('! refusing to patch -- this is not the module this patch was '
            'built against:')
        for b in bad:
            log('    ' + b)
        return 2
    sp = spec(_img(main), width, height)
    if sp is None:
        log('  field buffer already %dx%d -- nothing to do' % (width, height))
        return 0
    for p in sp['patches']:
        log('  ' + p['name'])
    if dry_run:
        log('  (dry run -- nothing written)')
        return 0
    nso = nso_patcher.read_nso(Path(main))
    for line in nso_patcher.apply_spec(nso, sp):
        log('    ' + line)
    tmp = main + '.fbtmp'
    Path(tmp).write_bytes(nso_patcher.rebuild(nso))
    os.replace(tmp, main)
    return 0


# --------------------------------------------------------------------------
# description and diagnosis
# --------------------------------------------------------------------------
def describe(width: int, height: int, shader_scale: float = None) -> list[str]:
    n = height / float(STOCK_HEIGHT)
    s = shader_scale if shader_scale is not None else ws_scale(width, int(n))
    per_unit_x = width * s / 640.0
    per_unit_y = height / 480.0
    u = 640.0 / s if s else 0.0
    a = u / 480.0
    origin = width / 2.0 * (1.0 - s)
    out = [
        'field buffer        %d x %d      (%gx the stock 320x240)'
        % (width, height, n),
        'WS_SCALE            %.8f' % s,
        'visible game span   %.2f x 480 units   (x from %.1f to %.1f)'
        % (u, 320 - u / 2.0, 320 + u / 2.0),
        'aspect              %.4f   (%+.2f%% vs 16:9)'
        % (a, 100.0 * (a / (16 / 9.0) - 1.0)),
        'buffer px per unit  %.6f across, %.6f down   (%s)'
        % (per_unit_x, per_unit_y,
           'square pixels' if abs(per_unit_x - per_unit_y) < 1e-6
           else '** ANAMORPHIC -- the picture will be the wrong shape **'),
        'pixels per texel    %.4f   (%s)'
        % (2 * per_unit_x,
           'whole -- no resample, no beat'
           if abs(2 * per_unit_x - round(2 * per_unit_x)) < 1e-4
           else '** FRACTIONAL -- this will band **'),
        'game x=0 lands at   %.1f buffer px   (%s)'
        % (origin,
           'integer, texel-aligned' if abs(origin - round(origin)) < 1e-6
           else '** half-pixel, uniformly blurred **'),
    ]
    for h, label in ((720, 'handheld 720p'), (1080, 'docked 1080p')):
        out.append('%-19s 1 buffer px = %.2f screen px'
                   % (label, h * 16 / 9.0 / width))
    out.append('render targets      %.2f MB for %d of them   (%+.2f MB vs '
               'the stock 320x240)'
               % (memory_cost(width, height), TARGET_COUNT,
                  memory_delta(width, height)))
    return out


def diagnose(width: int, scale: float, screen_h: int = 720) -> list[str]:
    """
    Predict the band period for a buffer width and a shader scale that are
    ALREADY on the card -- for whatever combination exists, not for the
    matched pair `describe()` reports.

    A 16-texel background tile is rasterised into `32 * px_per_unit` buffer
    pixels. The sampling phase of a 16-into-N resample repeats every
    `N / gcd` pixels; the visible beat is that period times the fixed
    buffer-to-screen magnification.
    """
    from fractions import Fraction
    # The `#define` in the .glsl carries ~8 significant digits, so snap to
    # the nearby simple ratio before reasoning about periods. Without this a
    # rounding error of 5e-9 reports a beat period of a billion pixels
    # instead of "none", which is true and useless.
    px_per_unit = (Fraction(width)
                   * Fraction(scale).limit_denominator(4096) / 640)
    for n in (1, 2, 3, 4, 6, 8):
        if abs(float(px_per_unit) - n / 2.0) < 1e-4:
            px_per_unit = Fraction(n, 2)
            break
    tile_px = px_per_unit * 32
    mag = Fraction(screen_h * 16, 9) / width
    out = ['buffer %d wide, WS_SCALE %.8f' % (width, scale),
           '  %.4f buffer px per game unit  (whole pixels per texel needs '
           'a multiple of 0.5)' % float(px_per_unit),
           '  a 16-texel tile is rasterised into %.4f buffer px'
           % float(tile_px)]
    # p/q buffer pixels per source texel, lowest terms: q texels cover p
    # pixels and the sampling phase repeats every p pixels.
    #
    # A repeat is only a BEAT when both p and q exceed 1. q == 1 means every
    # texel is exactly p pixels wide -- uniform, no phase to drift. p == 1
    # means every pixel is exactly q texels -- an integer box, also uniform.
    # Getting this wrong reported an exact 3x magnification as a 3-pixel
    # band, which is the opposite of what it is.
    r = tile_px / 16
    p, q = r.numerator, r.denominator
    if p == 1 or q == 1:
        out.append('  %s buffer px per texel: whole ratio, uniform sampling, '
                   'no beat.  CLEAN.' % r)
        return out
    out.append('  %s buffer px per texel -> %d texels cover %d px, and the '
               'phase repeats every %d buffer px' % (r, q, p, p))
    out.append('  at %dp that is %.2f SCREEN PX  <- the band period'
               % (screen_h, float(p * mag)))
    out.append('  (%s)' % ('MINIFICATION -- information is thrown away every '
                           'frame, which is the shimmer' if r < 1
                           else 'magnification -- soft, but not aliased'))
    return out


def tile_window_minima(width: int, scale: int = 1) -> dict:
    """
    The `ff7nx_wsclamp` extents this width needs. HANDOFF-49 §2.1: the drawn
    span for origin 320, low extent L and high bias R is
    [(320-L)*2, (320+R)*2], and it must cover the visible span.
    """
    import math
    u = visible_units(width, scale)
    lo, hi = 320 - u / 2.0, 320 + u / 2.0
    return {'left': int(math.ceil(320 - lo / 2.0)),
            'right': int(math.ceil(hi / 2.0 - 320)),
            'top': 224, 'bottom': 16}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.strip().splitlines()[0],
        epilog='See HANDOFF-51 for the measurement this is built on.',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('module', help='path to exefs/main')
    ap.add_argument('--show', action='store_true',
                    help='decode what the module is set to now')
    ap.add_argument('--scale', type=int, metavar='N',
                    help='supersample preset: 1 = 428x240 (ships), '
                         '2 = 854x480, 3 = 1280x720 (exact 16:9)')
    ap.add_argument('--four-three', action='store_true',
                    help='with --scale: 320Nx240N, for a 4:3 build')
    ap.add_argument('--size', metavar='WxH',
                    help='an explicit buffer size, e.g. 428x240')
    ap.add_argument('--stock', action='store_true',
                    help='back to 320x240')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)

    if not os.path.exists(a.module):
        print('! %s does not exist' % a.module)
        return 2

    want = None
    if a.stock:
        want = (STOCK_WIDTH, STOCK_HEIGHT)
    elif a.size:
        try:
            w, h = (int(x) for x in a.size.lower().split('x'))
        except ValueError:
            print('! --size wants WxH, e.g. 428x240')
            return 2
        want = (w, h)
    elif a.scale is not None:
        p = preset(a.scale, widescreen=not a.four_three)
        if p is None:
            want = (STOCK_WIDTH, STOCK_HEIGHT)
        else:
            want = (p['width'], p['height'])

    if want is None or a.show:
        bad = verify_sites(a.module)
        size = read_size(a.module)
        print('  field buffer        : %s'
              % ('%d x %d%s' % (size[0], size[1],
                                '   (STOCK 4:3 native)'
                                if size == (STOCK_WIDTH, STOCK_HEIGHT) else '')
                 if size else 'undecodable'))
        if bad:
            print('  ! site check failed:')
            for b in bad:
                print('      ' + b)
            return 2
        if size:
            n = max(1, size[1] // STOCK_HEIGHT)
            print()
            for line in describe(*size):
                print('  ' + line)
            need = tile_window_minima(size[0], n)
            print('  tile window needs   : left >= %d, right >= %d'
                  % (need['left'], need['right']))
        return 0

    print('== field buffer -> %d x %d ==' % want)
    rc = apply(a.module, want[0], want[1], dry_run=a.dry_run)
    if rc:
        return rc
    print()
    for line in describe(*want):
        print('  ' + line)
    n = max(1, want[1] // STOCK_HEIGHT)
    print()
    print('  The two vertex shaders MUST carry')
    print('      #define WS_SCALE %.8f' % ws_scale(want[0], n))
    print('  or the picture will be the wrong shape. A build does both.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
