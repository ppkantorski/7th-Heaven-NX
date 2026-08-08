#!/usr/bin/env python3
"""
ff7nx_framing.py -- the field FRAMING half of 16:9.

Companion to ff7nx_fieldwide.py, which does the content half (parallax clip
points and the camera clamp). This one widens the field's viewport rect from
640 to 854 game units and makes the driver treat that as a full-width view
rather than a rescale.

WHY THIS IS NOT ANOTHER ATTEMPT AT THE DRIVER
---------------------------------------------
Attempts 1, 2 and 3 all failed because they changed the DRIVER: the logical
width in `gfx_drv_init`, or `gfx_drv_setviewport`'s arguments in a cave. The
driver only decides how an already-drawn picture maps to the screen, so every
one of them came out as a rescale.

The framing is not in the driver. It is four `push imm32` in game logic:

    x86 0x60D810 -> main +0x9296C0    set_field_viewport(x, y, w, h)
                                        -> [0xCFF1E0..0xCFF1EC]
    x86 0x60D837 -> main +0x9297C0    field_set_mode(?, mode)
        mode 2   set_field_viewport(  0,   0, 640, 448)   <- widened here
        mode 0   set_field_viewport(  0,   0, 320, 224)
        mode 1   set_field_viewport(160, 120, 320, 224)

and the UI reads an entirely DIFFERENT set of globals:

    field_draw_everything 0x63A60B, call 0x63A9D1 -> [0xCFF1E0..EC]
    menu_draw_everything  0x6CC9D3, call 0x6CCD54 -> [0xDC105C..68]

which is why "field wide, UI at 4:3" costs nothing here. The UI is untouched
because it never reads the globals this module changes.

WHY 854 AND NOT FFNx's (-107, 854)
-----------------------------------
FFNx offsets the viewport to a NEGATIVE x. Our engine cannot represent that:
`gfx_drv_setviewport` is unsigned end to end -- four `ucvtf` and five
`umull`/`lsr`. Passing -107 yields `_41 = 13421773.0`, which is exactly
`(0xFFFFFF95 + 427 - 320) / 320`. Measured, not guessed. See
README-widescreen-v7.

The formulation used instead needs no negative number and is exact:

    x = 0, w = 854, and game_w = 854 for that call

giving `_11 = 1.0` (no geometry rescale) and `_41 = 0.0` (centred), with the
device rect growing to fill the real 16:9 window -- because `scale_x` is the
4:3 logical width times 1.5 and the real window is the same height times
16/9 times 1.5, and (16/9)/(4/3) is exactly 854/640. Verified at 720p, 1080p
and 1440p by `verify_framing.py`, which executes the real words.

Note what is NOT here: `gfx_drv_init`'s logical width is left alone.
Attempt 1's four words are not needed and must stay off, or the target
widens twice.
"""
import os
import struct

FRAMING_ENV = 'SEVENTH_NX_FRAMING'

GAME_WIDTH_43 = 640
WIDE_VIEWPORT_WIDTH = 854              # FFNx src/widescreen.h

# ---------------------------------------------------------------- part 1
#
# The field viewport width for mode 2 -- the 2x path fields actually use.
# `mov w8, #0x280` feeds the third argument of the set_field_viewport call
# at +0x929910; the matching height `mov w8, #0x1c0` at +0x9298BC is left
# alone (vertical is the separate `enable_uncrop` question).
FIELD_VIEWPORT_PATCH = {
    'name': 'field mode-2 viewport width 640 -> 854',
    'va': 0x09298D4,
    'expect': '08 50 80 52',           # mov w8, #0x280
    'set':    'C8 6A 80 52',           # mov w8, #0x356
}

# ---------------------------------------------------------------- part 2
#
#   +10D67F4  ldr w11, [x9, #0x954]    game_w -> w11
#   +10D67F8  ldr w9,  [x9, #0x958]    game_h -> w9        <- HOOK
#   +10D67FC  str xzr, [x13, #0x7f0]
#
# ff7nx_cave.emit_hooked lays `body` out BEFORE the displaced instruction, so
# the hook sits one word past the game_w load: w11 is then already loaded
# when the body runs. The displaced word is a plain `ldr`, position-
# independent, which is what ff7nx_cave.hook() insists on.
HOOK_VA = 0x10D67F8
HOOK_ORIG = 0xB9495929                 # ldr w9, [x9, #0x958]


def _cmp_imm(rn, imm):
    return 0x71000000 | (imm << 10) | (rn << 5) | 31


def _csel(rd, rn, rm, cond):
    return 0x1A800000 | (rm << 16) | (cond << 12) | (rn << 5) | rd


def cave_body():
    """
    Two words:  if (w == 854) game_w = w

    With w11 already holding obj->game_w, that turns `_11 = w / game_w` into
    1.0 and `_41` into 0 for the widened field rect only.

    Liveness, checked rather than assumed:
      * w2 (the `w` argument) is live to the end -- stored at +0x10D68AC.
      * w11 was freshly loaded at +0x10D67F4; its earlier use in the
        device-rect maths finished at +0x10D67F0.
      * `cmp` clobbers NZCV, and the next flag consumer (`cmp w9, #3` at
        +0x10D6860) sets its own.
    """
    return [
        _cmp_imm(2, WIDE_VIEWPORT_WIDTH),      # cmp  w2, #0x356
        _csel(11, 2, 11, 0x0),                 # csel w11, w2, w11, eq
    ]


# 854 is used as a sentinel, so it must not be a width anything else passes.
# These are every constant width seen across the 44 call sites of
# engine_gfx_setviewport_sub_66067A. Asserted at build time, not trusted.
STOCK_VIEWPORT_WIDTHS = (120, 160, 224, 240, 320, 448, 480, 640)


def _hex(word):
    return ' '.join('%02X' % b for b in struct.pack('<I', word))


def enabled():
    """Off unless explicitly switched on. Never defaults to true."""
    return os.environ.get(FRAMING_ENV, '').strip().lower() in (
        '1', 'true', 'on', 'yes')


def viewport_spec():
    return {
        'name': 'field viewport 640 -> 854 (16:9)',
        'patches': [dict(FIELD_VIEWPORT_PATCH)],
    }


def cave_patches(img, starts, log=lambda *_: None):
    """
    Words for the game_w cave, placed in reclaimed alignment padding.

    Returns {va: word}, including the `b cave` written over the hook site.
    Uses the padding pool, NOT the 2,464-byte tail gap the 60 FPS caves live
    in, so this costs that budget nothing.
    """
    import ff7nx_cave
    if WIDE_VIEWPORT_WIDTH in STOCK_VIEWPORT_WIDTHS:
        raise ValueError('854 collides with a stock viewport width -- the '
                         'sentinel is no longer safe')
    pool = ff7nx_cave.HolePool(img, starts=starts)
    out, entry = ff7nx_cave.emit_hooked(pool, HOOK_VA, HOOK_ORIG, cave_body())
    log('  game_w cave: %d words in padding, entry +%#x'
        % (len(cave_body()) + 2, entry))
    log('  (the 60 FPS cave region is not touched)')
    return out


def apply_to_nso(src, dest, log=lambda *_: None):
    """
    Apply both parts to `main` at `src`, writing `dest`. True on success.

    MUST RUN AFTER apply_fps_patches AND ON ITS OUTPUT. The 60 FPS pass
    rewrites exefs/main; a pass that starts from the stock module silently
    reverts all of it.

    Every original byte is verified by nso_patcher before anything is
    written, and the cave's holes are re-checked as still zero in the module
    being patched at the moment of patching.
    """
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    from pathlib import Path
    try:
        import nso_patcher
        import nxmap
    except ImportError as exc:
        log(f'! framing: cannot import {exc}')
        return False
    try:
        nso = nso_patcher.read_nso(Path(src))
        applied = nso_patcher.apply_spec(nso, viewport_spec())

        m = nxmap.Main(src)
        words = cave_patches(m.img, set(m.arm_starts), log)
        applied += nso_patcher.apply_spec(nso, {
            'name': 'setviewport game_w cave',
            'patches': [
                {'name': 'hook -> cave' if va == HOOK_VA else 'cave word',
                 'va': va,
                 'expect': _hex(struct.unpack_from('<I', m.img, va)[0]),
                 'set': _hex(word)}
                for va, word in sorted(words.items())
            ],
        })
        Path(dest).write_bytes(nso_patcher.rebuild(nso))
    except Exception as exc:
        log(f'! framing: {exc}')
        return False
    for line in applied:
        log('  ' + line)
    return True


if __name__ == '__main__':
    import sys
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)

    def dis(w):
        for i in md.disasm(struct.pack('<I', w), 0):
            return '%s %s' % (i.mnemonic, i.op_str)
        return '??'

    print('field viewport patch')
    p = FIELD_VIEWPORT_PATCH
    old = struct.unpack('<I', bytes(int(b, 16) for b in p['expect'].split()))[0]
    new = struct.unpack('<I', bytes(int(b, 16) for b in p['set'].split()))[0]
    print('  +%#09x  %-22s -> %-22s' % (p['va'], dis(old), dis(new)))
    print('\ngame_w cave body')
    for w in cave_body():
        print('  %08X  %s' % (w, dis(w)))
    print('  %08X  %s   (displaced)' % (HOOK_ORIG, dis(HOOK_ORIG)))
    print('\nhook at +%#x, returns to +%#x' % (HOOK_VA, HOOK_VA + 4))
    sys.exit(0)
