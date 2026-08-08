#!/usr/bin/env python3
r"""
ff7nx_modelcull.py -- widen the field model cull to the 16:9 frame.

THE SYMPTOM
===========
NPCs and party members pop into existence near the left and right edges
instead of already being there when the edge scrolls past them.  The cull
illusion breaks: you see the moment they are switched on.

THE CAUSE
=========
`field_do_draw_3d_model` decides per model, per frame, whether to draw it at
all, by testing its screen position against a fixed box around the field
viewport point.  The box is the 4:3 frame.  Widescreen made the frame 33%
wider without widening the box, so a model is switched off while it is still
inside the picture -- and switched on again a few pixels later, in view.

FFNx fixes exactly this, and says so in one line
(`src/ff7/widescreen.cpp:156`):

    replace_function(ff7_externals.field_culling_model_639252,
                     ff7::field::ff7_field_do_draw_3d_model);

and `src/ff7/field/model.cpp:36`:

    bool ff7_field_do_draw_3d_model(short x, short y)
    {
        if(*ff7_externals.field_bg_flag_CC15E4) return 1;
        int left_offset_x  =  40 + (widescreen_enabled ? abs(wide_viewport_x) - 50 : 0);
        int right_offset_x = 400 + (widescreen_enabled ? abs(wide_viewport_x) - 50 : 0);
        return x > CFF204->x - left_offset_x && x < CFF204->x + right_offset_x &&
               y > CFF204->y - 120        && y < CFF204->y + 460;
    }

THE SITE, IN THIS PORT
======================
x86 `ff7_en` 0x639252 recompiles to +0x9EC330..+0x9EC4D8, one for one.  All
four constants are plain ADD/SUB immediates and the two globals are formed in
the open:

    +0x9EC364  mov  w19, #0xf204 ; movk w19, #0xcf, lsl #16     -> 0xCFF204
    +0x9EC330  movk w0,  #0xcc,  lsl #16                        -> 0xCC15E4

    +0x9EC388  sub  w9, w8, #0x78     y - 120     unchanged by FFNx
    +0x9EC3E8  add  w8, w8, #0x1cc    y + 460     unchanged by FFNx
    +0x9EC43C  sub  w9, w8, #0x28     x -  40     <-- LEFT
    +0x9EC49C  add  w8, w8, #0x190    x + 400     <-- RIGHT

Same order as the x86 (y first, then x), same immediates, same registers.

THE NUMBER
==========
FFNx's 16:9 is `wide_viewport_width = 854`, `wide_viewport_x = -107`, so the
term is `abs(-107) - 50 = 57` on each side.  This build's frame is
`game-x -106.67 .. 746.67` (the build log's own line, from `WS_SCALE 0.75`) --
the same 16:9 span, so the same 57.

    left   40 -> 97      right  400 -> 457

The `- 50` is FFNx's, not a rounding: the stock box is already generous
relative to the 4:3 frame, so widening it by the full half-widening would push
the cull further out than it needs to go and cost fill rate on models that
genuinely cannot be seen.  Copying the reference implementation exactly is the
point -- this is the one part of the frame work that has a known-good
implementation to copy, so it gets copied rather than re-derived.

THE PATCH
=========
Two words, both pure imm12 edits.  The register fields, the opcode and the
shift bit are untouched, so the instruction cannot become something else:

    +0x9EC43C   09 A1 00 51   sub w9, w8, #0x28
             -> 09 85 01 51   sub w9, w8, #0x61       (97)

    +0x9EC49C   08 41 06 11   add w8, w8, #0x190
             -> 08 25 07 11   add w8, w8, #0x1c9      (457)

WHAT IT IS NOT
==============
It does not touch the VERTICAL cull.  FFNx leaves `y - 120` and `y + 460`
alone, and so does this: the frame is not taller than 4:3, only wider.
Anything vertical belongs to `ff7nx_letterbox`, which owns the 480-unit
window and the tile origins.

It is also not the BACKGROUND cull.  That is `field_layerN_pick_tiles` and it
belongs to `ff7nx_wsclamp`, which already widened it (the build log's
`horizontal O=320 L=400 R=64 -> span -160 .. 768   covers`).  Models and tiles
are culled by two entirely separate tests; widening the tiles was necessary
and did nothing for the models.

    python3 ff7nx_modelcull.py <main> --verify
    python3 ff7nx_modelcull.py <main> --show
    python3 ff7nx_modelcull.py <main> --apply
    python3 ff7nx_modelcull.py <main> --revert
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
for _p in (_HERE, os.path.join(_HERE, '7th_heaven_nx')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

MODELCULL_ENV = 'SEVENTH_NX_MODEL_CULL'

# ---------------------------------------------------------------------------
# the site
# ---------------------------------------------------------------------------
FUNC          = 0x9EC330        # field_do_draw_3d_model, x86 0x639252
LEFT_SITE     = 0x9EC43C        # sub w9, w8, #imm     x - left_offset
RIGHT_SITE    = 0x9EC49C        # add w8, w8, #imm     x + right_offset

STOCK_LEFT    = 40
STOCK_RIGHT   = 400
WIDE_DELTA    = 57              # FFNx: abs(wide_viewport_x) - 50, 16:9 -> 107-50
WIDE_LEFT     = STOCK_LEFT + WIDE_DELTA     # 97
WIDE_RIGHT    = STOCK_RIGHT + WIDE_DELTA    # 457

# The two words that must be there for this to be the right function at all:
# the vertical pair FFNx does NOT change. If either moved, refuse.
SIG = [
    (0x9EC388, 0x5101E109),     # sub w9, w8, #0x78     y - 120
    (0x9EC3E8, 0x11073108),     # add w8, w8, #0x1cc    y + 460
    (0x9EC364, 0x529E4093),     # mov w19, #0xf204   \  0xCFF204,
    (0x9EC368, 0x72A019F3),     # movk w19,#0xcf,16  /  the viewport point
]

IMM12_MASK = 0xFFF << 10


def _imm12(word: int) -> int:
    return (word >> 10) & 0xFFF


def _set_imm12(word: int, value: int) -> int:
    if not 0 <= value <= 0xFFF:
        raise ValueError(f'{value} does not fit in an imm12')
    return (word & ~IMM12_MASK) | (value << 10)


def _is_addsub_imm(word: int) -> bool:
    """ADD/SUB (immediate), 32- or 64-bit, S or not, with shift == 0."""
    return ((word & 0x7F800000) in (0x11000000, 0x31000000,
                                    0x51000000, 0x71000000)
            and ((word >> 22) & 1) == 0)


def _fmt(word: int) -> str:
    return ' '.join(f'{b:02X}' for b in struct.pack('<I', word))


def _text(path) -> bytes:
    import nso_tool
    return nso_tool.parse_nso(str(path))['segments']['.text']['data']


def w32(t: bytes, va: int) -> int:
    return struct.unpack_from('<I', t, va)[0]


# ---------------------------------------------------------------------------
# state / anchors
# ---------------------------------------------------------------------------
def state(t: bytes) -> dict:
    left, right = w32(t, LEFT_SITE), w32(t, RIGHT_SITE)
    return {
        'left_word': left, 'right_word': right,
        'left': _imm12(left), 'right': _imm12(right),
        'wide': _imm12(left) == WIDE_LEFT and _imm12(right) == WIDE_RIGHT,
        'stock': _imm12(left) == STOCK_LEFT and _imm12(right) == STOCK_RIGHT,
    }


def _is_branch(word: int) -> bool:
    return (word & 0xFC000000) == 0x14000000


def owned_by_moviecull(t: bytes) -> bool:
    """
    True when `ff7nx_moviecull` has replaced both cull sites with caves.

    That module gates the two bounds on `movie_object->is_playing` and takes
    the words over completely -- the 16:9 pair this module writes becomes the
    cave's not-playing branch. So a `b` here is a HEALTHY state, not damage,
    and neither --verify nor --apply may treat it as corruption. Which module
    owns a word is the thing ff7nx_status.py exists to keep straight; this is
    the same rule stated at the other end.
    """
    return _is_branch(w32(t, LEFT_SITE)) and _is_branch(w32(t, RIGHT_SITE))


def check_anchors(t: bytes) -> list[str]:
    bad = []
    if owned_by_moviecull(t):
        # Only the four function-identity anchors still apply; the two cull
        # words belong to ff7nx_moviecull now.
        for va, want in SIG:
            got = w32(t, va)
            if got != want:
                bad.append(f'+{va:#09x} is {got:08X}, expected {want:08X} -- '
                           f'field_do_draw_3d_model is not where this thinks '
                           f'it is')
        return bad
    for va, want in SIG:
        got = w32(t, va)
        if got != want:
            bad.append(f'+{va:#09x} is {got:08X}, expected {want:08X} -- '
                       f'field_do_draw_3d_model is not where this thinks it is')

    for va, name, allowed in ((LEFT_SITE, 'left', (STOCK_LEFT, WIDE_LEFT)),
                              (RIGHT_SITE, 'right', (STOCK_RIGHT, WIDE_RIGHT))):
        got = w32(t, va)
        if not _is_addsub_imm(got):
            bad.append(f'{name} cull +{va:#09x} is {got:08X}, not an ADD/SUB '
                       f'immediate with shift 0')
            continue
        if _imm12(got) not in allowed:
            bad.append(f'{name} cull +{va:#09x} offset is {_imm12(got)}, '
                       f'not {allowed[0]} (stock) or {allowed[1]} (16:9)')

    # the edit must not disturb anything but the immediate
    for va, target in ((LEFT_SITE, WIDE_LEFT), (RIGHT_SITE, WIDE_RIGHT)):
        got = w32(t, va)
        if _is_addsub_imm(got):
            rebuilt = _set_imm12(got, _imm12(got))
            if rebuilt != got:
                bad.append(f'imm12 round-trip failed at +{va:#09x}')
    return bad


# ---------------------------------------------------------------------------
# plan / apply
# ---------------------------------------------------------------------------
def plan(t: bytes, revert: bool) -> tuple[list[dict], list[str]]:
    patches, notes = [], []
    if owned_by_moviecull(t):
        notes.append('  model cull: both sites are ff7nx_moviecull caves. '
                     'Revert that module first if you need to change these.')
        return patches, notes
    for va, name, stock, wide in ((LEFT_SITE, 'left', STOCK_LEFT, WIDE_LEFT),
                                  (RIGHT_SITE, 'right', STOCK_RIGHT, WIDE_RIGHT)):
        cur = w32(t, va)
        want_v = stock if revert else wide
        new = _set_imm12(cur, want_v)
        if new == cur:
            notes.append(f'  model cull {name:5s}: already {want_v}')
            continue
        patches.append({
            'name': f'field model cull {name} offset {_imm12(cur)} -> {want_v}',
            'va': hex(va),
            'expect': _fmt(cur),
            'set': _fmt(new),
        })
        notes.append(f'  model cull {name:5s} {_imm12(cur):3d} -> {want_v:3d} '
                     f'@ +{va:#09X}')
    return patches, notes


def apply(main, revert=False, log=print) -> int:
    import nso_patcher
    main = Path(main)
    t = _text(main)

    bad = check_anchors(t)
    if bad:
        for b in bad:
            log('  ! ' + b)
        log('  refusing to write.')
        return 1

    patches, notes = plan(t, revert)
    for n in notes:
        log(n)
    if not patches:
        log('  nothing to do -- module already in the requested state')
        return 0

    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(nso, {'name': 'ff7nx_modelcull',
                                             'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.modelcull-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    st = state(_text(main))
    log('  read back from the written module:')
    log(f'    +{LEFT_SITE:#09X}  left  offset  {st["left"]}')
    log(f'    +{RIGHT_SITE:#09X}  right offset  {st["right"]}')
    log(f'  a model is now drawn for x in '
        f'[point.x-{st["left"]}, point.x+{st["right"]}]  '
        f'({st["left"] + st["right"]} units wide)')
    return 0


def show(main, log=print) -> int:
    t = _text(main)
    st = state(t)
    log(f'  {main}')
    log(f'    +{LEFT_SITE:#09X}  {_fmt(st["left_word"])}  left  offset '
        f'{st["left"]:3d}{"   (stock 4:3)" if st["left"] == STOCK_LEFT else "   <- 16:9"}')
    log(f'    +{RIGHT_SITE:#09X}  {_fmt(st["right_word"])}  right offset '
        f'{st["right"]:3d}{"   (stock 4:3)" if st["right"] == STOCK_RIGHT else "   <- 16:9"}')
    log(f'    box width {st["left"] + st["right"]} units '
        f'(4:3 needs 440, 16:9 needs {WIDE_LEFT + WIDE_RIGHT})')
    bad = check_anchors(t)
    for b in bad:
        log('    ! ' + b)
    log('    anchors: ' + ('OK' if not bad else f'{len(bad)} FAILED'))
    return 1 if bad else 0


# ---------------------------------------------------------------------------
# build-time gate
# ---------------------------------------------------------------------------
def enabled() -> bool:
    """
    ON with 16:9, OFF at 4:3, overridable.

    The numbers are widescreen numbers -- FFNx guards the same change with
    `widescreen_enabled` -- so at 4:3 this is not a smaller improvement, it
    is wrong: it would keep models alive outside a frame that never widened.
    """
    v = os.environ.get(MODELCULL_ENV)
    if v is not None:
        return v not in ('', '0', 'off', 'false')
    try:
        import ff7nx_ws
        return ff7nx_ws.enabled()
    except Exception:
        return False


def verify(main=None, log=print) -> int:
    fails = []

    def ck(cond, what):
        log(f'    {"ok  " if cond else "FAIL"}  {what}')
        if not cond:
            fails.append(what)

    log('  arithmetic and encodings (no module needed):')
    ck(WIDE_LEFT == 97 and WIDE_RIGHT == 457,
       f'FFNx 16:9 offsets: {STOCK_LEFT}+{WIDE_DELTA}={WIDE_LEFT}, '
       f'{STOCK_RIGHT}+{WIDE_DELTA}={WIDE_RIGHT}')
    ck(WIDE_DELTA == 107 - 50, 'the delta is abs(wide_viewport_x) - 50 = 57')
    # the encodings must MEAN what they are named -- FINDINGS-88 §8d
    for cur, want, name in ((0x5100A109, WIDE_LEFT, 'left  sub w9,w8'),
                            (0x11064108, WIDE_RIGHT, 'right add w8,w8')):
        new = _set_imm12(cur, want)
        ck(_imm12(new) == want and (new & ~IMM12_MASK) == (cur & ~IMM12_MASK),
           f'{name}: {cur:08X} -> {new:08X} really carries {want} and '
           f'changes nothing else')
    ck(_imm12(_set_imm12(0x5100A109, STOCK_LEFT)) == STOCK_LEFT,
       'the revert direction restores 40 exactly')

    if main is None:
        log('')
        log(f'  {len(fails)} failure(s)' if fails else '  all checks pass')
        return 1 if fails else 0

    t = _text(main)
    log('')
    log('  against the module:')
    for b in check_anchors(t):
        ck(False, b)
    if not fails:
        ck(True, 'the vertical pair is stock (y-120, y+460) -- FFNx leaves it')
        ck(True, 'the viewport point 0xCFF204 is formed in the open at +0x9EC364')
        ck(True, 'both cull sites are ADD/SUB imm12, shift 0')
        ck(True, 'both offsets are either the stock or the 16:9 value')

    if owned_by_moviecull(t):
        log('')
        log('    both cull sites are ff7nx_moviecull caves -- that module '
            'owns them now')
        ck(True, 'the four function-identity anchors still match')
        log('')
        log(f'  {len(fails)} failure(s)' if fails else '  all checks pass')
        return 1 if fails else 0

    st = state(t)
    ck(st['wide'] != st['stock'], 'module is in exactly one known state')
    p_on, _ = plan(t, revert=False)
    p_off, _ = plan(t, revert=True)
    ck(len(p_on) + len(p_off) == 2, 'exactly one direction is a no-op from here')

    for name, va, word in (('moved y-120 anchor', SIG[0][0], 0xD503201F),
                           ('moved 0xCFF204 anchor', SIG[2][0], 0xD503201F),
                           ('left cull that is not add/sub', LEFT_SITE, 0xD503201F),
                           ('an unknown left offset', LEFT_SITE,
                            _set_imm12(0x5100A109, 123))):
        mut = bytearray(t)
        struct.pack_into('<I', mut, va, word)
        ck(bool(check_anchors(bytes(mut))), f'{name} is refused')

    log('')
    log(f'  {len(fails)} failure(s)' if fails else '  all checks pass')
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('main', nargs='?')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--revert', action='store_true')
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--verify', action='store_true')
    a = ap.parse_args(argv)

    if a.verify or not (a.apply or a.revert or a.show):
        print('ff7nx_modelcull -- the model cull box is still 4:3')
        print('')
        return verify(a.main, log=print)
    if a.show:
        return show(a.main)
    if not a.main:
        ap.error('need a path to exefs/main')
    return apply(a.main, revert=a.revert)


if __name__ == '__main__':
    raise SystemExit(main())
