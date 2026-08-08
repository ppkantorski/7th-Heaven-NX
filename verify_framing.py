#!/usr/bin/env python3
"""
Execute the game_w cave's REAL WORDS inside gfx_drv_setviewport and read out
what the function writes -- before any of it goes near a module.

This exists because `ws_emu.run()` only emulates setviewport's own 0x154
bytes, so a cave placed in the padding pool is outside its window and would
never execute. Here the emulated window is extended and the cave is placed
just past the function's `ret`, purely so the BODY can be judged by what it
does. The shipped cave goes in the padding pool at a different address; the
body is identical and that is the part under test.

    python3 verify_framing.py
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import arm64emu                                                 # noqa: E402
import nxmap                                                    # noqa: E402
import ws_emu                                                   # noqa: E402

SETVIEWPORT = 0x10D6760

# The hook goes one instruction PAST the game_w load, not on it.
#
#   +10D67F4  ldr w11, [x9, #0x954]    game_w  -> w11
#   +10D67F8  ldr w9,  [x9, #0x958]    game_h  -> w9      <- HOOK HERE
#   +10D67FC  str xzr, [x13, #0x7f0]
#
# ff7nx_cave.emit_hooked lays out `body` BEFORE the displaced instruction, so
# hooking the game_w load itself would run the compare before the value
# existed. Hooking the next word means w11 is already loaded when the body
# runs, and the displaced word (`ldr w9, ...`) is position-independent, which
# is what ff7nx_cave.hook() requires.
HOOK_VA = 0x10D67F8
HOOK_ORIG = 0xB9495929           # ldr w9, [x9, #0x958]
RETURN_TO = HOOK_VA + 4

WIDE_W = 854                     # the sentinel: FFNx's wide_viewport_width
M32 = 0xFFFFFFFF


# --------------------------------------------------------------- encoding
def _cmp_imm(rn, imm):
    return 0x71000000 | (imm << 10) | (rn << 5) | 31


def _csel(rd, rn, rm, cond):
    return 0x1A800000 | (rm << 16) | (cond << 12) | (rn << 5) | rd


def _b(frm, to):
    return 0x14000000 | (((to - frm) >> 2) & 0x03FFFFFF)


def cave_body():
    """
    Two words. Semantics, with w11 already holding obj->game_w:

        if (w == 854) game_w = w

    so `_11 = w / game_w` becomes 1.0 and `_41` becomes 0 for the widened
    field rect, and NOTHING else changes -- the menu passes 640, field modes
    0 and 1 pass 320, and every other call site passes 640/480 or the
    object's own fields.

    Register liveness, checked rather than assumed:
      * w2 (the `w` argument) is live to the end of the function -- it is
        stored at +0x10D68AC.
      * w11 is freshly loaded with game_w at +0x10D67F4; its earlier use in
        the device-rect maths finished at +0x10D67F0.

    `cmp` clobbers NZCV. Safe: the next flag consumer is `cmp w9, #3` at
    +0x10D6860, which sets its own, and nothing in between reads them.
    """
    return [
        _cmp_imm(2, WIDE_W),            # cmp  w2, #0x356
        _csel(11, 2, 11, 0x0),          # csel w11, w2, w11, eq
    ]


# ------------------------------------------------------------- emulation
def run(x, y, w, h, scale_x, scale_y, game_w, game_h, mode, patched):
    """
    Same as ws_emu.run, but with a wider emulated window so a cave can be
    placed and executed, and returning the two extra rects setviewport
    writes at +0x7F0/+0x7F8 -- which the game_w change also moves, and which
    ws_emu does not surface.
    """
    img = nxmap.Main(os.path.join(HERE, 'exefs', 'main')).img
    lo = SETVIEWPORT
    hi = SETVIEWPORT + 0x154 + 0x40             # room for the cave
    words = list(struct.unpack('<%dI' % ((hi - lo) // 4), img[lo:hi]))

    if patched:
        cave = SETVIEWPORT + 0x154              # just past `ret`
        # exactly what ff7nx_cave.emit_hooked builds: body, displaced, branch
        laid = cave_body() + [HOOK_ORIG]
        for k, word in enumerate(laid):
            words[(cave - lo) // 4 + k] = word
        words[(cave - lo) // 4 + len(laid)] = _b(cave + 4 * len(laid),
                                                 RETURN_TO)
        words[(HOOK_VA - lo) // 4] = _b(HOOK_VA, cave)

    mem = arm64emu.Mem()
    for slot, target in ws_emu.PTRS.items():
        mem.setu(slot, target, 8)
    mem.setu(ws_emu.PTRS[0x12CE578], scale_x, 4)
    mem.setu(ws_emu.PTRS[0x12CE580], scale_y, 4)
    mem.setu(ws_emu.PTRS[0x12CE3E8], ws_emu.OBJ, 8)
    mem.setu(ws_emu.PTRS[0x12CE1F8], mode, 4)
    mem.setu(ws_emu.OBJ + 0x954, game_w, 4)
    mem.setu(ws_emu.OBJ + 0x958, game_h, 4)

    cpu = ws_emu.Cpu(mem)
    cpu.set(0, x & M32, True)
    cpu.set(1, y & M32, True)
    cpu.set(2, w & M32, True)
    cpu.set(3, h & M32, True)
    cpu.sp = 0x50000000
    cpu.run(lo, words, max_steps=4000)

    dst, mtx = 0x40001000, 0x40002000
    return {
        'x1': mem.u(dst + 0x800, 4), 'x2': mem.u(dst + 0x808, 4),
        'full_w': mem.u(dst + 0x7F8, 4),
        '_11': round(ws_emu.b2f(mem.u(mtx + 0xA8, 4)), 6),
        '_22': round(ws_emu.b2f(mem.u(mtx + 0xBC, 4)), 6),
        '_41': round(ws_emu.b2f(mem.u(mtx + 0xD8, 4)), 6),
        '_42': round(ws_emu.b2f(mem.u(mtx + 0xDC, 4)), 6),
    }


# ------------------------------------------------------------------ cases
#
# Every rect the game actually passes, from the 44 call sites of
# engine_gfx_setviewport_sub_66067A. The point of the table is that only ONE
# row is allowed to move.
CASES = [
    ('field mode 2 (0,0,640,448)',    0,   0, 640, 448, 640, False),
    ('field mode 2 WIDENED  ->854',   0,   0, 854, 448, 640, True),
    ('field mode 0 (0,0,320,224)',    0,   0, 320, 224, 640, False),
    ('field mode 1 (160,120,320,224)', 160, 120, 320, 224, 640, False),
    ('menu (0,0,640,480)',            0,   0, 640, 480, 640, False),
    ('obj-sized (0,0,640,480)',       0,   0, 640, 480, 640, False),
    ('battle-ish (0,0,320,240)',      0,   0, 320, 240, 640, False),
    ('fullscreen (0,0,640,480)',      0,   0, 640, 480, 640, False),
]

FAIL = []
N = [0]


def check(cond, what):
    N[0] += 1
    if not cond:
        FAIL.append(what)


def main():
    scale_x, scale_y = 1440, 1080          # 720p, STOCK 4:3 logical width
    window = 1920                          # the real 16:9 window

    print('720p, stock logical width (gfx_drv_init NOT patched).')
    print('scale_x %d, real window %d\n' % (scale_x, window))
    print('%-32s %-9s %-9s %-9s %s' % ('rect passed', '_11', '_41', '_22',
                                       'device rect'))
    print('-' * 78)

    for label, x, y, w, h, gw, should_move in CASES:
        off = run(x, y, w, h, scale_x, scale_y, gw, 480, 1, patched=False)
        on = run(x, y, w, h, scale_x, scale_y, gw, 480, 1, patched=True)
        moved = off != on
        flag = ''
        if should_move and not moved:
            flag = '   <-- FAILED TO MOVE'
        if not should_move and moved:
            flag = '   <-- MOVED, MUST NOT'
        check(moved == should_move,
              '%s: moved=%s expected=%s' % (label, moved, should_move))
        print('%-32s %-9s %-9s %-9s %d..%d%s'
              % (label, on['_11'], on['_41'], on['_22'],
                 on['x1'], on['x2'], flag))

    print()
    # the widened row, in detail
    wide = run(0, 0, 854, 448, scale_x, scale_y, 640, 480, 1, patched=True)
    base = run(0, 0, 640, 448, scale_x, scale_y, 640, 480, 1, patched=False)
    check(wide['_11'] == 1.0, 'widened _11 is 1.0 (no geometry rescale)')
    check(wide['_41'] == 0.0, 'widened _41 is 0.0 (centred)')
    check(wide['_22'] == base['_22'], 'widened _22 unchanged (vertical intact)')
    check(wide['_42'] == base['_42'], 'widened _42 unchanged')
    fill = (wide['x2'] - wide['x1']) / window
    check(abs(fill - 1.0) < 0.001,
          'device rect fills the real window (got %.4f)' % fill)
    px_before = (base['x2'] - base['x1']) / 640.0
    px_after = (wide['x2'] - wide['x1']) / 854.0
    check(abs(px_before - px_after) < 0.01,
          'pixels per game unit unchanged: %.4f -> %.4f' % (px_before, px_after))

    print('widened field rect, in detail')
    print('  _11 %s  (1.0 = no geometry rescale -- what attempts 1-3 broke)'
          % wide['_11'])
    print('  _41 %s  (centred)' % wide['_41'])
    print('  device rect %d..%d = %.4f of the %d window'
          % (wide['x1'], wide['x2'], fill, window))
    print('  px per game unit  %.4f -> %.4f  (unchanged)' % (px_before, px_after))
    print('  +0x7F8 full-rect word %#x -> %#x' % (base['full_w'], wide['full_w']))

    # resolution independence: the identity is scale_x * 854/640 == window
    print('\nresolution independence')
    for hgt in (720, 1080, 1440):
        sx = int(hgt * 4 / 3 * 1.5)
        win = int(hgt * 16 / 9 * 1.5)
        r = run(0, 0, 854, 448, sx, int(hgt * 1.5), 640, 480, 1, patched=True)
        f = (r['x2'] - r['x1']) / win
        check(abs(f - 1.0) < 0.001, '%dp fills the window (%.4f)' % (hgt, f))
        print('  %-6s scale_x %-6d window %-6d rect %d..%-6d fill %.4f'
              % ('%dp' % hgt, sx, win, r['x1'], r['x2'], f))

    # 854 must be a safe sentinel: nothing else may pass it
    print('\nsentinel safety: no stock call site passes w == 854')
    stock_widths = {640, 448, 320, 224, 240, 480, 160, 120}
    check(WIDE_W not in stock_widths,
          '854 collides with a stock viewport width')
    print('  stock widths seen across the 44 call sites: %s'
          % sorted(stock_widths))
    print('  854 is not among them.')

    print()
    if FAIL:
        for f in FAIL:
            print('FAIL  %s' % f)
        print('\n%d/%d checks failed' % (len(FAIL), N[0]))
        return 1
    print('%d checks passed' % N[0])
    return 0


if __name__ == '__main__':
    sys.exit(main())
