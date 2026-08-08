#!/usr/bin/env python3
"""
ff7nx_scissor.py -- RETIRED. This patch is a no-op. See HANDOFF-57 §2.

    python3 ff7nx_scissor.py <exefs/main | sdout> --show      still works
    python3 ff7nx_scissor.py <exefs/main | sdout> --apply     REFUSED

DO NOT SHIP THIS. `--apply` is disabled and will not write. It is kept
because the disassembly below is correct and is the derivation HANDOFF-57
§2 rests on; only the conclusion drawn from it was wrong.

WHY IT IS A NO-OP
=================
`gfx_drv_setviewport` computes both rects against the CURRENT RENDER TARGET
size (`[[0x12CE578]]` / `[[0x12CE580]]`), scaling by a hardcoded /640, /480:

    FULL     rect = (0,0) .. (tW * game_width/640,  tH * game_height/480)
    VIEWPORT rect = (tW*x/640, tH*y/480) .. (tW*(x+w)/640, tH*(y+h)/480)

`game_width` is 640 and `game_height` is 480 in every build this repo ships
-- `ff7nx_ws.apply_module()` explicitly retired the `game_w := 854` patch on
a hardware result. So FULL = (0,0)..(tW,tH), and for the full-screen
viewport that the field, menus, battle and credits all issue,
VIEWPORT = (0,0)..(tW,tH) as well.

    The two rects are bit-identical. This patch swaps a load of one for a
    load of the other and produces the same value.

Even under the retired ws-3d set it would be a no-op: FULL would be
1.334*tW, which clamps to the render target width tW, which is what the
VIEWPORT rect already is.

WHAT THE CREDIT BLEED ACTUALLY IS
=================================
`custom_shaders/wide_screen/tlmain_vv.glsl` ends with

    gl_Position.x *= WS_SCALE;      // 0.75

after the projection. Clip-space rejection at |clip.x| <= clip.w therefore
now admits |x_proj| <= w/0.75, and the visible game-x range widens from
0..640 to -106.7..746.7 -- which that shader's own header states. FF7 stages
2D off-screen and slides it in; 107 units of "off-screen" on each side are
now inside the clip volume.

The fix is a scissor set to the central 4:3 band (tW/8 .. 7tW/8), which is
what FFNx's `Renderer::setScissor` computes per driver_mode. That rect does
not exist in this module and cannot be produced by re-pointing a load. See
HANDOFF-57 §4.3.

--------------------------------------------------------------------------
ORIGINAL DOCSTRING FOLLOWS -- the disassembly is right, the conclusion is not
--------------------------------------------------------------------------

THE SYMPTOM
===========
With 16:9 on, text that should be off-screen is drawn in the black bars
during the opening credits, and smears there. On PC with FFNx the same
credits do not bleed. That difference is the whole clue, and it points at
exactly one thing FFNx does that this port does not.

WHAT FFNx DOES
==============
`common_setviewport` (FFNx common.cpp:1447) ends with

    newRenderer.setScissor(_x, _y, _w, _h);

Every 2D draw is clipped to the rect the GAME asked for. FF7 stages sliding
text off-screen -- it positions a string at game-x -200, slides it in, and
the 640-wide viewport clips whatever has not arrived yet. That clip is what
makes "off-screen" mean anything.

WHAT THIS PORT DOES
===================
`gfx_drv_setviewport` (+0x10D6760, driver table entry 142) computes TWO
rects into the render state object at `[0x12CE548]`:

    +0x800 / +0x808   the VIEWPORT rect, scaled from (x, y, w, h)
    +0x7F0 / +0x7F8   (0, 0) and the FULL device size, from
                      game_obj->game_width (+0x954) and game_height (+0x958)

and the per-draw helper (+0x10D9D70, README-46 2) picks between them by
vertex type:

    +0x10D9EE4   TLVERTEX (2D):  x1 = [x8, #0x7F0]   \  the FULL frame
    +0x10D9EF0                   x2 = [x8, #0x7F8]   /
    +0x10D9F3C   bl +0x11320E0   -> vtable +0x188     the scissor
    +0x10D9F4C   x1 = [x26, #0x800] \  the viewport rect
    +0x10D9F50   x2 = [x26, #0x808] /
    +0x10D9F54   bl +0x11320F0   -> vtable +0x190     the viewport

(The 3D branch passes `x1 = xzr` to +0x188 at +0x10D9F28 -- a null scissor,
which is what makes +0x188 the scissor and +0x190 the viewport rather than
the other way round.)

So the scissor for 2D is set to the WHOLE RENDER TARGET, every draw. At 4:3
that is harmless: the full frame and the viewport are both 640 units wide,
so "clip to the frame" and "clip to the viewport" are the same clip and
nothing off-screen is ever inside either.

WHY 16:9 BREAKS IT
==================
`ff7nx_widescreen` / README-47 3a replaces the game_width load that feeds
the full rect:

    +0x10D67F4   ldr w11, [x9, #0x954]   ->   mov w11, #0x356      (854)

so the full frame becomes 854 units wide while the credits keep their 640
viewport. 107 units on each side that were off-screen -- and full of text
waiting to slide in -- are now inside the scissor and get drawn.

The margins are also never repainted between frames outside the 4:3 region,
so what is drawn there stays: the smear.

THE PATCH
=========
Point the 2D scissor at the viewport rect, which is what FFNx clips to.

| VA | was | becomes | |
|---|---|---|---|
| +0x10D9EEC | `F943F901` `ldr x1, [x8, #0x7F0]` | `F9440101` `ldr x1, [x8, #0x800]` | top-left |
| +0x10D9EF0 | `F943FD02` `ldr x2, [x8, #0x7F8]` | `F9440502` `ldr x2, [x8, #0x808]` | bottom-right |

Same instruction, same base register, same destination -- only the 12-bit
offset changes, from the full-frame pair to the viewport pair four and eight
bytes further on. Nothing is relocated and no branch is added.

WHAT IT DOES NOT CLIP
=====================
The field background is TLVERTEX too, so it goes through this same scissor.
It is not clipped away, because README-47 3a widens the field's own
viewport to match:

    +0x9298D4   mov w8,  #0x280 -> #0x356    field mode-2 viewport width 854
    +0x929938   mov w24, #0x140 -> #0x1AB    the two half-widths 427

**Those patches must be in the build.** They ship with the "16:9 widescreen"
dropdown value `ws-3d`. On a build without them the field viewport is still
640 units and this patch will clip the widescreen background back to 4:3 --
which is a visible, obvious, instantly reversible failure, not a subtle one.

Menus and battle keep their 640-wide viewport and are already drawn inside
it, so clipping to it removes nothing that was visible. That is the same
place FFNx clips them.

RELATION TO ff7nx_bgcolor.py
============================
Different bug, different mechanism, and they are independent:

* **this** stops things being DRAWN in the margin (the credit bleed).
* **ff7nx_bgcolor** changes what the margin is CLEARED to (the flat green /
  tan / grey, which is the field background palette's entry 0).

Both can be on at once; neither needs the other. See HANDOFF-56.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import nxmap                                                     # noqa: E402

SCISSOR_ENV = 'SEVENTH_NX_CLIP_2D'

TITLE_ID = '0100A5B00BDC6000'
SDOUT_MAIN = os.path.join('atmosphere', 'contents', TITLE_ID, 'exefs', 'main')

# (va, stock word, patched word, what it is)
WORDS = [
    (0x10D9EEC, 0xF943F901, 0xF9440101,
     'ldr x1, [x8, #0x7F0]  ->  ldr x1, [x8, #0x800]   scissor top-left'),
    (0x10D9EF0, 0xF943FD02, 0xF9440502,
     'ldr x2, [x8, #0x7F8]  ->  ldr x2, [x8, #0x808]   scissor bottom-right'),
]

# Untouched words that must be present for this to be the right module.
ANCHORS = [
    (0x10D9EE8, 0xF942A508, 'render state object load [0x12CE548]'),
    (0x10D9F3C, 0x94016069, 'bl +0x11320E0  (vtable +0x188, the scissor)'),
    (0x10D9F4C, 0xF9440341, 'viewport top-left     ldr x1, [x26, #0x800]'),
    (0x10D9F50, 0xF9440742, 'viewport bottom-right ldr x2, [x26, #0x808]'),
    (0x10D9F54, 0x94016067, 'bl +0x11320F0  (vtable +0x190, the viewport)'),
    (0x10D67DC, 0xF90401A9, 'setviewport stores the viewport rect at +0x800'),
    (0x10D67FC, 0xF903F9BF, 'setviewport stores (0, 0) at +0x7F0'),
    (0x10D682C, 0xF903FDAA, 'setviewport stores the full size at +0x7F8'),
]

# Not required, but reported: the field viewport widening this depends on.
FIELD_WIDE = [
    (0x9298D4, 0x52805008, 0x52806AC8, 'field viewport width 640 -> 854'),
    (0x929938, 0x52802818, 0x52803578, 'field half-width 320 -> 427'),
    (0x10D67F4, 0xB949552B, 0x52806ACB, 'setviewport game_w 640 -> 854'),
]


def enabled():
    """Is the 2D clip switched on? Off unless explicitly set."""
    return os.environ.get(SCISSOR_ENV, '').strip().lower() in (
        '1', 'true', 'on', 'yes')


def resolve_main(path):
    """Accept either exefs/main itself or the root of an SD tree."""
    if os.path.isdir(path):
        cand = os.path.join(path, SDOUT_MAIN)
        if os.path.exists(cand):
            return cand
        raise SystemExit('no %s under %s' % (SDOUT_MAIN, path))
    return path


def _hex(word):
    return ' '.join('%02X' % b for b in struct.pack('<I', word))


def _word(text, va):
    return struct.unpack_from('<I', text, va)[0]


def state(text):
    """'stock', 'patched', or 'unknown'."""
    got = [_word(text, va) for va, _, _, _ in WORDS]
    if got == [w for _, w, _, _ in WORDS]:
        return 'stock'
    if got == [w for _, _, w, _ in WORDS]:
        return 'patched'
    return 'unknown'


def verify_anchors(text, log=print):
    ok = True
    for va, want, what in ANCHORS:
        got = _word(text, va)
        if got != want:
            log('  ! +0x%07X %s: expected %08X, found %08X'
                % (va, what, want, got))
            ok = False
    return ok


def field_is_wide(text):
    """
    True if the field's own viewport was already widened to 854 units.

    Without that, clipping 2D to the viewport clips the widescreen field
    background back to 4:3. Reported, not enforced -- a diagnostic build may
    legitimately want to see exactly that.
    """
    return all(_word(text, va) == patched
               for va, _stock, patched, _what in FIELD_WIDE)


def show(path, log=print):
    m = nxmap.Main(path)
    text = m.text
    st = state(text)
    log('module %s' % path)
    log('  2D clipped to viewport: %s'
        % {'stock': 'NOT installed  (2D is clipped to the whole frame)',
           'patched': 'INSTALLED',
           'unknown': 'UNRECOGNISED -- do not patch'}[st])
    for va, stock, new, what in WORDS:
        got = _word(text, va)
        mark = '  ' if got == stock else ('->' if got == new else '??')
        log('  %s +0x%07X  %08X   %s' % (mark, va, got, what))
    log('  anchors: %s'
        % ('pass' if verify_anchors(text, lambda *_: None) else 'FAIL'))
    wide = field_is_wide(text)
    log('  field viewport widened to 854: %s' % ('yes' if wide else 'NO'))
    if not wide:
        log('    ! with this patch on and the field viewport still 640 units,')
        log('      the widescreen field background will be clipped back to')
        log('      4:3. Build with 16:9 widescreen = ws-3d first.')


def build_spec(install=True):
    patches = []
    for va, stock, new, what in WORDS:
        a, b = (stock, new) if install else (new, stock)
        patches.append({'name': what,
                        'va': '0x%X' % va,
                        'expect': _hex(a),
                        'set': _hex(b)})
    return {'name': '2D scissor -> viewport', 'patches': patches}


def _write(path, out, spec, log, dry):
    import nso_patcher
    try:
        nso = nso_patcher.read_nso(Path(path))
        applied = nso_patcher.apply_spec(nso, spec)
        data = nso_patcher.rebuild(nso)
    except Exception as exc:                                   # noqa: BLE001
        log('! %s' % exc)
        log('  nothing was written; the module is unchanged')
        return False
    log('  %d word(s) verified and applied' % len(applied))
    if dry:
        log('  (dry run, nothing written)')
        return True
    with open(out, 'wb') as f:
        f.write(data)
    log('  wrote %s' % out)
    return True


RETIRED = (
    'ff7nx_scissor is RETIRED: this patch is a no-op. The full-frame rect '
    '(+0x7F0/+0x7F8) and the viewport rect (+0x800/+0x808) hold the same '
    'value for any full-screen viewport, because game_width is 640 in every '
    'build this repo ships. See HANDOFF-57 §2. The credit bleed is '
    'WS_SCALE in tlmain_vv.glsl -- HANDOFF-57 §4.')


def apply(path, out, log=print, dry=False):
    log('! ' + RETIRED)
    log('  refusing to write. Use --show to read the derivation.')
    return False


def _apply_disabled(path, out, log=print, dry=False):
    m = nxmap.Main(path)
    st = state(m.text)
    if st == 'patched':
        log('  already installed; nothing to do')
        return True
    if st == 'unknown' or not verify_anchors(m.text, log):
        log('! this module is not the one the offsets were derived from; '
            'refusing to patch')
        return False
    if not field_is_wide(m.text):
        log('  ! field viewport is still 640 units -- the widescreen field '
            'background will be clipped to 4:3 by this patch. Patching '
            'anyway; revert if that is what you see.')
    for va, stock, new, what in WORDS:
        log('  +0x%07X  %08X -> %08X   %s' % (va, stock, new, what))
    return _write(path, out, build_spec(True), log, dry)


def revert(path, out, log=print, dry=False):
    m = nxmap.Main(path)
    st = state(m.text)
    if st == 'stock':
        log('  not installed; nothing to do')
        return True
    if st == 'unknown':
        log('! module is in neither state; refusing to touch it')
        return False
    return _write(path, out, build_spec(False), log, dry)


def apply_to_nso(src, dst, log=lambda *_: None):
    """build.py's entry point."""
    return apply(src, dst, log=log)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='clip 2D drawing to the viewport, as FFNx does')
    ap.add_argument('main',
                    help='path to exefs/main, OR the sdout/ directory')
    ap.add_argument('--show', action='store_true')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--revert', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('-o', '--out', help='write here (default: in place)')
    a = ap.parse_args(argv)
    src = resolve_main(a.main)
    out = a.out or src
    if a.apply and a.revert:
        ap.error('--apply and --revert are mutually exclusive')
    if src != a.main:
        print('found %s' % src)
    if a.apply:
        return 0 if apply(src, out, dry=a.dry_run) else 1
    if a.revert:
        return 0 if revert(src, out, dry=a.dry_run) else 1
    show(src)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
