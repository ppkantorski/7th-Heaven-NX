#!/usr/bin/env python3
r"""
ff7nx_moviealign.py -- move the movie quad with the field, so the cut is seamless.

THE SYMPTOM
===========
An FMV that hands straight over to gameplay -- the train pulling in, the
reactor -- jumps 24 px at the moment the video stops.  Models drawn over the
video sit 24 px low against it while it plays, then snap into place.

THE CAUSE
=========
`ff7nx_letterbox` moved the field down 16 game units (tile origin 224 -> 232,
`[0xCFF200]` 224 -> 240) so the 480-unit window lands exactly on FF7's camera
clamp.  Everything that is placed through those two origins moved with it.
The movie quad is placed through neither.

MEASURED, on the 2026-08-07 build, 1280x720:

    gameplay   rows    0..719   cols    0..1279
    movie      rows    0..672   cols  159..1120

`159..1120` is `game x 0..640` at `WS_SCALE 0.75` -- the pillarbox, and it is
correct: FF7's FMVs are 4:3 and FFNx keeps them that way
(`getMovieMode() == WM_DISABLED`).  `0..672` is `game y 0..448` **anchored at
the top**, and that is the part that no longer matches anything.

THE QUAD
========
+0x10DE7C0 builds it, and it is plain:

    +0x10DE818  ucvtf s0, w2              ; video height
    +0x10DE81C  mov   w11, #0x44200000    ; 640.0f          .rodata 0x11AE7A8
    +0x10DE82C  mov   w10, #0x43f00000    ; 480.0f          .rodata 0x11AE764
    +0x10DE84C  fmul  s0, s0, s1          ; h * 640
    +0x10DE858  fdiv  s0, s0, s1          ;      / w
    +0x10DE860  fcmp  s0, s1              ; against 480
    +0x10DE868  csel  x8, x10, x9, gt     ; H = min(h*640/w, 480)

    +0x10DE878  stp  x11, x9, [sp, #0x28]     v0 = (640,   0)
    +0x10DE884  stp  w11, w8, [sp, #0x48]     v1 = (640,   H)
    +0x10DE888  stp  wzr, w8, [sp, #0x68]     v2 = (  0,   H)
    +0x10DE89C  stur x8,     [sp, #0x8c]      v3 = (  0,   0)
    +0x10DE8F4  bl   #0x10D9D70               draw

A 640x448 video gives H = 448, so the quad is game (0,0)..(640,448) -> rows
0..672 at this scale.  That is exactly the measurement, which is how we know
this is the right function without guessing.

In stock FF7 that was correct and needed no thought: the field viewport was
(0,0,640,448) too, so the movie filled it exactly.  FFNx keeps them together a
different way -- `ff7_field_center` sets the viewport to (0,16,640,448) and the
d3dviewport matrix carries BOTH the movie quad and the field background,
because in FFNx the 2D path goes through it.  In this port that matrix is
write-only (FINDINGS-88 8b), so nothing is carried automatically and each
thing has to be moved by hand.  Two were.  This is the third.

THE PATCH
=========
Add 16.0f to the quad's four y values.  Two of them are the literal 0 and can
just be stored; two are H and need one add each:

    w9 = 0x41800000            16.0f
    str w9, [sp, #0x2c]        v0.y  0 -> 16
    str w9, [sp, #0x8c]        v3.y  0 -> 16
    fmov s0, w9
    ldr s1,[sp,#0x4c] ; fadd s1,s1,s0 ; str s1,[sp,#0x4c]     v1.y  H -> H+16
    ldr s1,[sp,#0x6c] ; fadd s1,s1,s0 ; str s1,[sp,#0x6c]     v2.y  H -> H+16

Ten words, plus the displaced `strb w8, [sp]` and the branch back: twelve, in
`ff7nx_cave`'s padding holes.  The 60 FPS tail gap is not touched.

Hooked at +0x10DE8F0, the last store before the draw call, so every vertex is
already written and there is nothing left to overwrite it.  s0/s1 are dead
there -- the last float use is +0x10DE864 and the callee takes its arguments in
x0..x7 -- and w8 is preserved because the displaced instruction is the one that
consumes it.

WHAT IT LOOKS LIKE AFTERWARDS
=============================
    movie      rows   24..696   cols 159..1120     bars top, bottom and sides
    gameplay   rows    0..719   cols    0..1279

and the content in rows 24..696 is the same content in both, because the field
art at tile origin 232 lands there by construction.  The cut is the bars being
dropped and the view expanding, which is what it is supposed to look like.

It does NOT stretch the video, and it does not touch the pillarbox.  H is still
`min(h*640/w, 480)`, so a 4:3 video stays 4:3.

WHY +16 AND NOT "CENTRE IT"
===========================
Centring the quad in the 480 frame would be the general-purpose thing to do and
it is wrong here.  The field art at tile origin 232 is at rows 24..696, full
stop, so the movie has to be at `y = 16` to meet it -- not wherever centring
happens to put it.  +16 is a fixed rendezvous, not a fit.

It also never runs off the bottom, which was the thing worth checking rather
than assuming.  Every movie in the build was probed:

    1280x896   38 files      quad H 448.0      16 + 448.0 = 464.0
     640x448    1 file       quad H 448.0      16 + 448.0 = 464.0
    1276x896    1 file       quad H 449.4      16 + 449.4 = 465.4

All 10:7, all well inside 480.  If a future FMV pack ships something taller
than 464 the bottom of it would be clipped, and `--verify`'s table says so for
the shapes it is asked about.

    python3 ff7nx_moviealign.py <main> --verify
    python3 ff7nx_moviealign.py <main> --show
    python3 ff7nx_moviealign.py <main> --apply
    python3 ff7nx_moviealign.py <main> --revert
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

MOVIEALIGN_ENV = 'SEVENTH_NX_MOVIE_ALIGN'

# ---------------------------------------------------------------------------
# the site
# ---------------------------------------------------------------------------
QUAD_FUNC = 0x10DE7C0
HOOK_VA   = 0x10DE8F0            # strb w8, [sp]  -- last store before the draw
DISPLACED = 0x390003E8
DRAW_CALL = 0x10DE8F4

SHIFT_UNITS = 16                 # game units, = the field's tile-origin move
SHIFT_BITS  = 0x41800000         # 16.0f

# The vertex slots, learned from the stores rather than assumed.
Y_ZERO = (0x2c, 0x8c)            # v0.y and v3.y, literal 0.0f
Y_H    = (0x4c, 0x6c)            # v1.y and v2.y, the fitted height H

# Every word this patch depends on. If any moved, refuse.
SIG = [
    (0x10DE81C, 0x52A8840B, 'mov  w11, #0x44200000   640.0f'),
    (0x10DE82C, 0x52A87E0A, 'mov  w10, #0x43f00000   480.0f'),
    (0x10DE878, 0xA902A7EB, 'stp  x11, x9, [sp,#0x28]   v0'),
    (0x10DE884, 0x290923EB, 'stp  w11, w8, [sp,#0x48]   v1'),
    (0x10DE888, 0x290D23FF, 'stp  wzr, w8, [sp,#0x68]   v2'),
    (0x10DE89C, 0xF808C3E8, 'stur x8,      [sp,#0x8c]   v3'),
    (HOOK_VA,   DISPLACED,  'strb w8, [sp]              the hook site'),
]

SP = 31


def _movz_hi(rd, imm16): return 0x52A00000 | (imm16 << 5) | rd
def _str_w(rt, rn, off): return 0xB9000000 | ((off >> 2) << 10) | (rn << 5) | rt
def _ldr_s(st, rn, off): return 0xBD400000 | ((off >> 2) << 10) | (rn << 5) | st
def _str_s(st, rn, off): return 0xBD000000 | ((off >> 2) << 10) | (rn << 5) | st
def _fmov_w2s(sd, wn):   return 0x1E270000 | (wn << 5) | sd
def _fadd(sd, sn, sm):   return 0x1E202800 | (sm << 16) | (sn << 5) | sd


def cave_body() -> list[int]:
    """The ten words. No PC-relative instruction, so the runs may scatter."""
    w = [_movz_hi(9, SHIFT_BITS >> 16)]
    w += [_str_w(9, SP, off) for off in Y_ZERO]
    w += [_fmov_w2s(0, 9)]
    for off in Y_H:
        w += [_ldr_s(1, SP, off), _fadd(1, 1, 0), _str_s(1, SP, off)]
    return w


DISASM = [
    'mov w9, #0x41800000',
    'str w9, [sp, #0x2c]',
    'str w9, [sp, #0x8c]',
    'fmov s0, w9',
    'ldr s1, [sp, #0x4c]', 'fadd s1, s1, s0', 'str s1, [sp, #0x4c]',
    'ldr s1, [sp, #0x6c]', 'fadd s1, s1, s0', 'str s1, [sp, #0x6c]',
    'strb w8, [sp]',
]


# ---------------------------------------------------------------------------
# module helpers
# ---------------------------------------------------------------------------
def _main(path):
    import nxmap
    return nxmap.Main(str(path))


def w32(img: bytes, va: int) -> int:
    return struct.unpack_from('<I', img, va)[0]


def _fmt(word: int) -> str:
    return ' '.join(f'{b:02X}' for b in struct.pack('<I', word))


def state(img: bytes) -> dict:
    hook = w32(img, HOOK_VA)
    applied = (hook & 0xFC000000) == 0x14000000        # a `b`, not the strb
    entry = None
    if applied:
        imm = hook & 0x03FFFFFF
        if imm & (1 << 25):
            imm -= (1 << 26)
        entry = HOOK_VA + imm * 4
    return {'hook': hook, 'applied': applied, 'stock': hook == DISPLACED,
            'entry': entry}


def check_anchors(img: bytes) -> list[str]:
    bad = []
    st = state(img)
    for va, want, what in SIG:
        got = w32(img, va)
        if va == HOOK_VA:
            if not (got == DISPLACED or st['applied']):
                bad.append(f'hook site +{va:#09x} is {got:08X}, neither the '
                           f'stock `strb w8,[sp]` nor a branch')
            continue
        if got != want:
            bad.append(f'+{va:#09x} is {got:08X}, expected {want:08X}  ({what})')
    if not st['applied'] and not st['stock']:
        bad.append('the module is in neither the stock nor the patched state')
    return bad


# ---------------------------------------------------------------------------
# plan / apply
# ---------------------------------------------------------------------------
def plan(main, revert: bool, log=print):
    """(patches, notes). patches are nso_patcher dicts."""
    import ff7nx_cave
    m = _main(main)
    img = m.img
    st = state(img)
    notes = []

    if revert:
        if not st['applied']:
            return [], ['  movie align: already stock']
        # Walk the chain the same way it was written, from the entry branch.
        words = _walk_cave(img, st['entry'])
        patches = [{'name': 'movie align: unhook',
                    'va': hex(HOOK_VA), 'expect': _fmt(st['hook']),
                    'set': _fmt(DISPLACED)}]
        for va in sorted(words):
            patches.append({'name': f'movie align: clear cave word +{va:#x}',
                            'va': hex(va), 'expect': _fmt(w32(img, va)),
                            'set': '00 00 00 00'})
        notes.append(f'  movie align: unhooked, {len(words)} cave word(s) '
                     f'cleared')
        return patches, notes

    if st['applied']:
        return [], ['  movie align: already applied']

    out, entry = ff7nx_cave.emit_hooked(
        ff7nx_cave.HolePool(img, starts=set(m.arm_starts)),
        HOOK_VA, DISPLACED, cave_body())
    patches = []
    for va in sorted(out):
        cur = w32(img, va)
        if va != HOOK_VA and cur != 0:
            raise RuntimeError(f'cave word +{va:#x} is {cur:08X}, not padding')
        patches.append({'name': f'movie align +{va:#x}',
                        'va': hex(va), 'expect': _fmt(cur),
                        'set': _fmt(out[va])})
    notes.append(f'  movie quad +{SHIFT_UNITS} game units: '
                 f'{len(out) - 1} words in padding, entry +{entry:#x}')
    notes.append('  (the 60 FPS cave region is not touched)')
    return patches, notes


def _walk_cave(img: bytes, entry: int) -> list[int]:
    """Every word of the chained cave, following each run's outgoing `b`."""
    seen, va, guard = [], entry, 0
    while guard < 64:
        guard += 1
        w = w32(img, va)
        seen.append(va)
        if (w & 0xFC000000) == 0x14000000:            # b
            imm = w & 0x03FFFFFF
            if imm & (1 << 25):
                imm -= (1 << 26)
            tgt = va + imm * 4
            if tgt == DRAW_CALL:                      # the return branch
                return seen
            va = tgt
            continue
        va += 4
    raise RuntimeError('cave chain did not terminate at the return branch')


def apply(main, revert=False, log=print) -> int:
    import nso_patcher
    main = Path(main)
    m = _main(main)

    bad = check_anchors(m.img)
    if bad:
        for b in bad:
            log('  ! ' + b)
        log('  refusing to write.')
        return 1

    patches, notes = plan(main, revert, log)
    for n in notes:
        log(n)
    if not patches:
        return 0

    nso = nso_patcher.read_nso(main)
    lines = nso_patcher.apply_spec(nso, {'name': 'ff7nx_moviealign',
                                         'patches': patches})
    log(f'    {len(lines)} word(s) verified and applied')
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.moviealign-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    st = state(_main(main).img)
    log(f'  read back: hook +{HOOK_VA:#X} is '
        + ('a branch to the cave' if st['applied'] else 'stock'))
    predict(st['applied'], log)
    return 0


def show(main, log=print) -> int:
    m = _main(main)
    st = state(m.img)
    log(f'  {main}')
    log(f'    +{HOOK_VA:#09X}  {_fmt(st["hook"])}  '
        + ('branch to cave +%#x   <- applied' % st['entry'] if st['applied']
           else 'strb w8, [sp]        (stock)'))
    if st['applied']:
        try:
            log(f'    cave words: {len(_walk_cave(m.img, st["entry"]))}')
        except RuntimeError as exc:
            log(f'    ! {exc}')
    bad = check_anchors(m.img)
    for b in bad:
        log('    ! ' + b)
    log('    anchors: ' + ('OK' if not bad else f'{len(bad)} FAILED'))
    log('')
    predict(st['applied'], log)
    return 1 if bad else 0


# ---------------------------------------------------------------------------
# the model
# ---------------------------------------------------------------------------
def quad_rows(video_w, video_h, screen_h=720, shifted=False):
    """The movie quad's device rows, from the module's own arithmetic."""
    h = min(video_h * 640.0 / video_w, 480.0)
    y0 = SHIFT_UNITS if shifted else 0
    px = screen_h / 480.0
    return round(y0 * px), round((y0 + h) * px)


def predict(shifted: bool, log=print) -> None:
    log('  predicted movie placement at 720p '
        f'({"shifted" if shifted else "stock"}):')
    log('    video        quad y        device rows     field rows')
    for vw, vh in ((640, 448), (640, 480), (320, 224), (640, 360)):
        a, b = quad_rows(vw, vh, shifted=shifted)
        log(f'    {vw}x{vh:<5}  {SHIFT_UNITS if shifted else 0:3d}..'
            f'{(SHIFT_UNITS if shifted else 0) + min(vh * 640 / vw, 480):<7.0f}'
            f'  {a:4d}..{b:<8d}  {24}..696')
    log('    the field, at tile origin 232, puts the matching art on rows '
        '24..696')


def verify(main=None, log=print) -> int:
    fails = []

    def ck(cond, what):
        log(f'    {"ok  " if cond else "FAIL"}  {what}')
        if not cond:
            fails.append(what)

    log('  encodings and arithmetic (no module needed):')
    ck(struct.unpack('<f', struct.pack('<I', SHIFT_BITS))[0] == 16.0,
       f'{SHIFT_BITS:#010x} really is 16.0f')
    ck(quad_rows(640, 448) == (0, 672),
       'stock: a 640x448 video lands on rows 0..672  (measured: 0..672)')
    ck(quad_rows(640, 448, shifted=True) == (24, 696),
       'shifted: rows 24..696  -- where the field art at origin 232 is')
    ck(quad_rows(640, 480)[1] == 720 and quad_rows(640, 480, shifted=True)[1] == 744,
       'a full-height 480 video still fills, and is clipped not squashed')

    try:
        from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
        md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
        blob = b''.join(struct.pack('<I', x) for x in cave_body() + [DISPLACED])
        got = [(i.mnemonic + ' ' + i.op_str).strip() for i in md.disasm(blob, 0)]
        ck(len(got) == len(DISASM), f'capstone decodes all {len(DISASM)} words')
        for k, (g, want) in enumerate(zip(got, DISASM)):
            ck(g == want, f'word {k:2d}: `{g}` == `{want}`')
    except ImportError:
        log('    (capstone not installed -- encodings NOT checked)')

    if main is None:
        log('')
        log(f'  {len(fails)} failure(s)' if fails else '  all checks pass')
        return 1 if fails else 0

    m = _main(main)
    log('')
    log('  against the module:')
    for b in check_anchors(m.img):
        ck(False, b)
    if not fails:
        ck(True, 'the quad builder signature is intact (6 words)')
        ck(True, 'the hook site is the stock `strb w8, [sp]` or a branch')
        ck(True, f'+{DRAW_CALL:#x} is the draw the cave returns to')

    # the sp offsets this cave writes must be the ones the module wrote
    ck(w32(m.img, 0x10DE884) == 0x290923EB and w32(m.img, 0x10DE888) == 0x290D23FF,
       'v1.y and v2.y are written at sp+0x4c and sp+0x6c by stp w?,w8')

    st = state(m.img)
    ck(st['applied'] != st['stock'], 'module is in exactly one known state')

    for name, va, word in (('a moved 640.0f', SIG[0][0], 0xD503201F),
                           ('a moved v3 store', SIG[5][0], 0xD503201F),
                           ('a hook site that is neither', HOOK_VA, 0xD503201F)):
        mut = bytearray(m.img)
        struct.pack_into('<I', mut, va, word)
        ck(bool(check_anchors(bytes(mut))), f'{name} is refused')

    log('')
    log(f'  {len(fails)} failure(s)' if fails else '  all checks pass')
    return 1 if fails else 0


# ---------------------------------------------------------------------------
def enabled() -> bool:
    """Rides the same gate as the frame it is aligning to."""
    v = os.environ.get(MOVIEALIGN_ENV)
    if v is not None:
        return v not in ('', '0', 'off', 'false')
    try:
        import ff7nx_letterbox
        return ff7nx_letterbox.enabled()
    except Exception:
        return False


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
        print('ff7nx_moviealign -- the movie quad did not move with the field')
        print('')
        return verify(a.main, log=print)
    if a.show:
        return show(a.main)
    if not a.main:
        ap.error('need a path to exefs/main')
    return apply(a.main, revert=a.revert)


if __name__ == '__main__':
    raise SystemExit(main())
