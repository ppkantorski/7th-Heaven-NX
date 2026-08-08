#!/usr/bin/env python3
"""
Remove the pillarbox: stop the renderer manufacturing a 4:3 logical width.

WHAT IS WRONG
-------------
`gfx_drv_init` (module +0x10D5150 -- Square Enix's MaterialSX shim, native
ARM64, not recompiled x86) asks the OS for the real screen size, stores the
real width, and then never uses it. Instead it builds a "logical" width by
forcing 4:3 onto the real HEIGHT:

    real_width  = get_device_width()      ; stored, then ignored
    real_height = get_device_height()
    logical_w   = (real_height * 320) / 240        ; == height * 4/3

Everything downstream measures against `logical_w`, and the difference
between it and the real width is drawn as black bars. On a 720p screen the
engine decides the picture is 960 wide and pillarboxes the remaining 320.

THE FIX
-------
`320/180` is exactly `16/9`, so changing the divisor from 240 to 180 makes
the logical width equal the real width on any 16:9 output -- 1280 at 720p,
1920 at 1080p -- without having to plumb the discarded real width through.

The compiler emitted the division as a magic multiply (`0x88888889`,
add-form, shift 7). An exact magic for 180 needs the PLAIN form and a
different shift, so four words move rather than two:

    +0x10D5284  mov  w8, #0x8889            -> mov  w8, #0x16c3
    +0x10D5288  movk w8, #0x8888, lsl #16   -> movk w8, #0x16c, lsl #16
    +0x10D52A4  add  w8, w8, w9             -> nop        (drop add-back)
    +0x10D52AC  asr  w10, w8, #7            -> mov  w10, w8  (shift 0)

`w9` is dead after +0x10D52A4 -- the next write to x9 is the `adrp` at
+0x10D52A8 -- so dropping the add-back is safe. +0x10D52B0's
`add w8, w10, w8, lsr #31` is left alone: with shift 0 the sign term is
always 0 for a positive screen height.

The arithmetic was verified by first reproducing the CURRENT constants as an
exact `/240` over every plausible height, then confirming the replacements
give an exact `/180`. See test_widescreen.py.

WHAT THIS DOES NOT DO
---------------------
The logical width feeds three things (all nine of its uses were traced):
the pillarbox margin, 2D flat-quad normalisation, and background fitting.

  * The bars go. That part simply works.
  * 2D UI is normalised against a wider box, so menus and boxes can shift
    or stretch. FFNx hit the same class of side effect on PC. Expected.
  * The background is FITTED to the logical box. A 320-wide background in a
    426-wide box is STRETCHED, not extended. Widescreen field backgrounds
    need content that is actually wider -- which is what Cosmos Limit
    Break's `flevel.lgp` sections provide, and why enabling that mod on its
    own changed nothing visible.

Not touched here: the 3D projection matrix. Models and the battle camera
still render a 4:3 slice of the world into the wider frame until that is
found (it is not in the driver/shim -- proven by exhaustion -- so it lives
in the recompiled game logic).
"""
import os
import struct

WIDESCREEN_ENV = 'SEVENTH_NX_WIDESCREEN'

# Module offsets == NSO virtual addresses for `main`. All four are in .text,
# well clear of the 60 FPS patch sites and of the code-cave region that
# starts at 0x1152660, so the two patch sets compose.
PATCHES = [
    {
        'name': 'logical-width divisor 240 -> 180, magic lo',
        'va': 0x10D5284,
        'expect': '28 11 91 52',      # mov  w8, #0x8889
        'set':    '68 D8 82 52',      # mov  w8, #0x16c3
    },
    {
        'name': 'logical-width divisor 240 -> 180, magic hi',
        'va': 0x10D5288,
        'expect': '08 11 B1 72',      # movk w8, #0x8888, lsl #16
        'set':    '88 2D A0 72',      # movk w8, #0x16c, lsl #16
    },
    {
        'name': 'drop the add-back (plain-form magic)',
        'va': 0x10D52A4,
        'expect': '08 01 09 0B',      # add  w8, w8, w9
        'set':    '1F 20 03 D5',      # nop
    },
    {
        'name': 'magic shift 7 -> 0',
        'va': 0x10D52AC,
        'expect': '0A 7D 07 13',      # asr  w10, w8, #7
        'set':    'EA 03 08 2A',      # mov  w10, w8
    },
]


# ---------------------------------------------------------------- mode 2
#
# `gfx_drv_setviewport` is module +0x10D6760 (driver master table index 142).
# Disassembled, it is FFNx's `common_setviewport` line for line, including
# the battle carve-out FFNx itself could not explain:
#
#     scaleX = [[0x12CE578]]           ; = logical_width * 1.5  (the target)
#     scaleY = [[0x12CE580]]           ; = real_height  * 1.5
#     vx = scaleX * x / 640            ; magic 0xCCCCCCCD, lsr #41
#     vw = scaleX * w / 640
#     vy = scaleY * y / 480            ; magic 0x88888889, lsr #40
#     vh = scaleY * h / 480
#     _11 = (float)w / game_width
#     _22 = (mode == 3) ? 1.0f : (float)h / game_height      <-- MODE_BATTLE
#     _41 =  ((x + w/2) - game_width /2) / (game_width /2)
#     _42 = -((y + h/2) - game_height/2) / (game_height/2)
#
# 640 and 480 are the engine's `game_width`/`game_height`, present only as
# those two magic divisors. Game space is 640 wide and maps onto the WHOLE
# render target.
#
# WHY MODE 1 STRETCHED, arithmetically
# ------------------------------------
# Mode 1 widens `logical_width`, and the target is `logical_width * 1.5`, so
# the target became 16:9. Nothing else changed: the game still asked for a
# 640-wide viewport, `_11` stayed 640/640 = 1, and game space still mapped
# onto the whole target. So the same 4:3 picture was painted across a 16:9
# surface. Every quad, menu and background scaled to fit -- exactly what you
# saw, everywhere, including the start screen.
#
# THE PROBE
# ---------
# Change the x divisor from 640 to 853 (= 640 * 4/3) while mode 1 keeps the
# target at 16:9. Game space then lands on 3/4 of the target, which is the
# original 4:3 width, at the original scale.
#
# PREDICTION, and the whole point of shipping it: the picture goes back to
# CORRECT PROPORTIONS -- no stretch anywhere -- sitting against the LEFT of
# the screen, with one black bar of about a quarter of the width on the
# RIGHT. Not centred; centring needs an offset there is no spare instruction
# for. If that is what you see, the model above is right and the remaining
# work is offset plus content. If it is stretched, or centred, or anything
# else, the model is wrong and this cost two words to find out.
#
# It is a measurement, not a feature.
SETVIEWPORT = 0x10D6760

# THE FIT
# -------
# `_11` is what scales the geometry, and it is `(float)w / (float)game_w`.
# The device rect built with the /640 magic is a clip region and moving it
# changes nothing on screen -- measured, see README-widescreen-v4.
#
# So the transform has to be on the ARGUMENTS, before anything reads them:
#
#     x' = x * 3/4 + 80        (80 = (640 - 480) / 2)
#     w' = w * 3/4             (480 = 640 * 3/4)
#
# Emulated against the real function on a 16:9 target, setviewport(80,0,
# 480,480) yields _11 = 0.75, _41 = 0.0 and a device rect of 240..1680 --
# the stock 4:3 rect, centred in 1920. Correct proportions, no stretch.
#
# HOOK SITE
# ---------
# +0x10D676C, `mov w10, #0xcccd`. Chosen because it is position-independent
# (the two `adrp`s before it are not, and an adrp executed from a code cave
# computes a different page) and because it sits before +0x10D677C, the
# first instruction to read w2. Nothing between the function entry and here
# touches w0 or w2.
HOOK_VA = 0x10D676C
HOOK_ORIG = 0x529999AA                                # mov w10, #0xcccd


def _add_lsl(rd, rn, rm, sh):
    return 0x0B000000 | (rm << 16) | (sh << 10) | (rn << 5) | rd


def _lsr_imm(rd, rn, sh):
    return 0x53007C00 | (sh << 16) | (rn << 5) | rd


def cave_body():
    """x' = x*3/4 + 80 (w0), w' = w*3/4 (w2). Touches nothing else."""
    import a64 as A
    return [
        _add_lsl(0, 0, 0, 1),        # add w0, w0, w0, lsl #1     x*3
        _lsr_imm(0, 0, 2),           # lsr w0, w0, #2             x*3/4
        A.add_imm(0, 0, 80),         # add w0, w0, #80
        _add_lsl(2, 2, 2, 1),        # add w2, w2, w2, lsl #1     w*3
        _lsr_imm(2, 2, 2),           # lsr w2, w2, #2             w*3/4
    ]


MODES = ('stretch', 'fit')


def mode():
    """
    '' (off), 'stretch' (mode 1 only), or 'probe' (mode 1 + the divisor).

    `1`/`true`/`on` still mean 'stretch' so an old settings.json keeps
    working and keeps meaning what it meant.
    """
    raw = os.environ.get(WIDESCREEN_ENV, '').strip().lower()
    if raw in ('1', 'true', 'on', 'stretch'):
        return 'stretch'
    if raw == 'fit':
        return 'fit'
    return ''


def _hex(word):
    return ' '.join('%02X' % b for b in struct.pack('<I', word))


def enabled():
    """Is any widescreen patch switched on? Off unless explicitly set."""
    return bool(mode())


def spec():
    """A patch spec for nso_patcher, which verifies every original byte."""
    return {
        'name': '16:9 (%s)' % (mode() or 'off'),
        'patches': [dict(p) for p in PATCHES],
    }


def cave_patches(img, starts, log=lambda *_: None):
    """
    Word patches for the `fit` cave, placed in reclaimed padding so the
    60 FPS cave budget is untouched. Returns {va: word}.

    Raises ff7nx_cave.NoRoom if the padding pool cannot take it -- which
    would mean something is very wrong, since it needs 7 words out of
    ~7,800 available.
    """
    import ff7nx_cave
    pool = ff7nx_cave.HolePool(img, starts=starts)
    out, entry = ff7nx_cave.emit_hooked(pool, HOOK_VA, HOOK_ORIG, cave_body())
    runs = sorted({va for va in out if va != HOOK_VA})
    log('  setviewport cave: %d word(s) across %d padding hole(s), '
        'entry +%#x' % (len(cave_body()) + 2, len(pool.used), entry))
    log('  (the 60 FPS cave region is not touched)')
    return out


def logical_width(height, patched=True):
    """
    What the engine will compute for a given screen height.

    Models the exact instruction sequence rather than the intent, so the
    tests check the encoding and not a restatement of the comment.
    """
    def s32(x):
        return x - (1 << 32) if x >> 31 else x
    w9 = ((height + (height << 2)) << 6) & 0xFFFFFFFF        # height * 320
    magic = 0x016C16C3 if patched else 0x88888889
    hi = ((s32(w9) * s32(magic)) & ((1 << 64) - 1)) >> 32
    w8 = hi & 0xFFFFFFFF if patched else (hi + w9) & 0xFFFFFFFF
    w10 = w8 if patched else (s32(w8) >> 7)
    return (w10 + (w8 >> 31)) & 0xFFFFFFFF


def apply_to_nso(src, dest, log=lambda *_: None):
    """
    Patch `main` at `src`, writing `dest`. Returns True on success.

    Every original byte is verified by nso_patcher before anything is
    written, so a different game version fails loudly instead of producing
    a module patched in the wrong place.
    """
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import nso_patcher
    except ImportError as exc:
        log(f'! widescreen: cannot import nso_patcher ({exc})')
        return False
    from pathlib import Path
    try:
        nso = nso_patcher.read_nso(Path(src))
        applied = nso_patcher.apply_spec(nso, spec())
        if mode() == 'fit':
            # The cave goes in reclaimed alignment padding, NOT in the
            # 2,464-byte tail gap the 60 FPS caves live in -- so this adds
            # nothing to that budget and cannot displace anything already
            # there. Placement is computed against the module as it stands
            # after the 60 FPS pass, and every hole is re-verified as still
            # zero at that moment.
            import nxmap
            m = nxmap.Main(src)
            words = cave_patches(m.img, set(m.arm_starts), log)
            applied += nso_patcher.apply_spec(nso, {
                'name': 'setviewport 4:3-in-16:9 cave',
                'patches': [
                    {'name': ('cave word' if va != HOOK_VA
                              else 'hook -> cave'),
                     'va': va,
                     'expect': _hex(struct.unpack_from('<I', m.img, va)[0]),
                     'set': _hex(word)}
                    for va, word in sorted(words.items())],
            })
        data = nso_patcher.rebuild(nso)
    except Exception as exc:                                   # noqa: BLE001
        log(f'! widescreen: {exc}')
        log('  nothing was written; the module is unchanged')
        return False
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(data)
    for line in applied:
        log('  ' + line)
    return True
