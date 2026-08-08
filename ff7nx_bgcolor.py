#!/usr/bin/env python3
"""
ff7nx_bgcolor.py -- force the frame clear colour to BLACK.

    python3 ff7nx_bgcolor.py <exefs/main> --show
    python3 ff7nx_bgcolor.py <exefs/main> --apply [-o OUT] [--dry-run]
    python3 ff7nx_bgcolor.py <exefs/main> --revert [-o OUT]

Two words, in place, no cave, no displaced instruction.

WHY -- and why HANDOFF-55 2.3 / 5.4 has to be re-opened first
============================================================
HANDOFF-55 5.4 scanned every B and BL in `.text`, found no branch to
`gfx_drv_clear` (+0x10D68D0) or its thunk (+0x10D68C0), and concluded the
clear is dead code. **That scan cannot find the callers, because there are
no direct branches to find.**

This repo's own `gfx_drv_table.txt` -- produced by `dump_gfx_table.py` from
the port's name-keyed import table at +0x12C9A70 -- lists them:

    143  0x10D68C0   gfx_drv_clear_all
    144  0x10D68D0   gfx_drv_clear

They are FUNCTION POINTERS. They are installed into FF7's `struct
gfx_driver` (FFNx ff7.h:2022, `gfx_clear_all *clear_all;`) and the
recompiled x86 game code calls them the way the PC game always did --
indirectly, through the driver struct, from the main loop. A B/BL scan is
blind to that by construction. Every entry in that table of 203 is invisible
to it, including `gfx_drv_flip` and `gfx_drv_begin_scene`, which unarguably
run every frame.

So "the clear is dead code in this build" is a false negative, and 2.3's
cave -- which added a SECOND call to the same clear, with the same colour --
was always going to change nothing on screen. It is not a negative result
about clearing. It is a redundant call.

WHAT THE CLEAR ACTUALLY DOES (disassembled, +0x10D68D0)
======================================================
    +0x10D68E8  adrp x8, 0x12CE000
    +0x10D68EC  ldr  x8, [x8, #0x3E8]        ; &game_obj
    +0x10D68F0  ldr  x8, [x8]                ; game_obj
    +0x10D68F8  ldr  w0, [x8, #0xA7C]        ; game_obj->gfx_driver_data
    +0x10D6900  bl   +0x10FC3A0              ; x86 ptr -> host ptr
    +0x10D6908  ldp  s8,  s9,  [x0, #0x08]   \  FOUR FLOATS: the clear colour
    +0x10D6910  ldp  s11, s10, [x0, #0x10]   /
    ...
    +0x10D6960  bl   +0x1132090              ; depth branch (vtable +0x108)
    +0x10D6978  bl   +0x1132170              ; clear depth to 1.0 (vtable +0xA8)
    +0x10D69A8  bl   +0x1132150              ; CLEAR COLOUR      (vtable +0x98)

`game_obj + 0xA7C` is `gfx_driver_data` -- FFNx ff7.h:1977, the field between
`field_A78` and `field_A80`. +0x10FC3A0 is the emulator's 32-bit-x86-pointer
-> host-pointer translation (`lsr w9, w0, #12` page index, `and w9, w0, #0xFFF`
page offset), which is also why it returns NULL for a null x86 pointer.

And `gfx_drv_setbg` (+0x10D6A00, table index 145) is where those four floats
come from -- it is a 16-byte copy and nothing else:

    +0x10D6A48  ldp  x8, x9, [x0]            ; the bgra_color the game passed
    +0x10D6A4C  stp  x8, x9, [x19, #8]       ; -> gfx_driver_data + 8

So: **the game sets a background colour, and the whole render target is
cleared to it every frame.** Inside 4:3 the field art covers it. Outside
4:3 -- the 16:9 margins -- nothing covers it, so the margin IS the clear
colour. That is a flat colour that changes when the game changes it, which
is exactly the reported symptom: green in the Sector 7 slums, tan in Wall
Market, grey at the Honey Bee Inn.

THE PATCH
=========
Make `setbg` store black instead of the colour it was handed.

| VA | was | becomes | |
|---|---|---|---|
| +0x10D6A48 | `A9402408` `ldp x8, x9, [x0]`      | `D503201F` `nop`                    | drop the load |
| +0x10D6A4C | `A900A668` `stp x8, x9, [x19, #8]` | `A900FE7F` `stp xzr, xzr, [x19,#8]` | store black |

Same instruction, same addressing mode, same length; only Rt and Rt2 change
to XZR. Nothing is relocated and no branch is added.

The dropped `ldp` also removes a latent null dereference: `setbg` reaches
+0x10D6A48 with `x0 = xzr` on the path at +0x10D6A44 when the colour pointer
it was handed is null.

WHAT EACH OUTCOME MEANS -- decide before the SD card goes in
===========================================================
* **Margins turn black.** Confirmed. The margin was the clear colour, the
  clear was never dead, and 4.1 is closed for the 93 art-less fields.
  Presentation is then what 4.1 asks for and no further module work is
  needed for them.
* **Margins are unchanged.** Then the flat colour is NOT the clear colour,
  and the clear is eliminated properly this time -- by changing what it
  paints, not by calling it twice. The next lead is the credits path
  (HANDOFF-55 2.4), and this patch should be reverted.

Either way it is two words and one build.

WHAT THIS DOES NOT TOUCH
========================
Nothing about promotion, page size, compaction or the field archive. It is a
module patch, so it is lost on rebuild and must be re-applied -- like every
other patch in the tree.
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

# The build reads this. Off unless explicitly set, so a build without it is
# byte-identical to one from before this file existed.
BLACK_MARGINS_ENV = 'SEVENTH_NX_BLACK_MARGINS'

# Where exefs/main lives inside an SD tree, so --sdout can find it.
TITLE_ID = '0100A5B00BDC6000'
SDOUT_MAIN = os.path.join('atmosphere', 'contents', TITLE_ID, 'exefs', 'main')

SETBG = 0x10D6A00

# (va, stock word, patched word, what it is)
WORDS = [
    (0x10D6A48, 0xA9402408, 0xD503201F, 'ldp x8, x9, [x0]  ->  nop'),
    (0x10D6A4C, 0xA900A668, 0xA900FE7F,
     'stp x8, x9, [x19, #8]  ->  stp xzr, xzr, [x19, #8]'),
]

# Untouched words that must be present for this to be the right module.
ANCHORS = [
    (0x10D6A00, 0xA9BE4FF4, 'gfx_drv_setbg prologue'),
    (0x10D6A1C, 0xB94A7D00, 'setbg: ldr w0, [x8, #0xa7c]  (gfx_driver_data)'),
    (0x10D68D0, 0x6DBB2BEB, 'gfx_drv_clear prologue'),
    (0x10D6908, 0x2D412408, 'clear: ldp s8, s9, [x0, #8]  (colour)'),
    (0x10D6910, 0x2D42280B, 'clear: ldp s11, s10, [x0, #0x10]  (colour)'),
]


def enabled():
    """Is the black-margin patch switched on? Off unless explicitly set."""
    return os.environ.get(BLACK_MARGINS_ENV, '').strip().lower() in (
        '1', 'true', 'on', 'yes')


def resolve_main(path):
    """
    Accept either exefs/main itself or the root of an SD tree.

    Typing the full atmosphere/contents/<title id>/exefs/main by hand is
    where the standalone test goes wrong, so let `sdout/` work too.
    """
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


def show(path, log=print):
    m = nxmap.Main(path)
    text = m.text
    st = state(text)
    log('module %s' % path)
    log('  black margins: %s' % {'stock': 'NOT installed',
                                 'patched': 'INSTALLED',
                                 'unknown': 'UNRECOGNISED -- do not patch'}[st])
    for va, stock, new, what in WORDS:
        got = _word(text, va)
        mark = '  ' if got == stock else ('->' if got == new else '??')
        log('  %s +0x%07X  %08X   %s' % (mark, va, got, what))
    log('  anchors: %s'
        % ('pass' if verify_anchors(text, lambda *_: None) else 'FAIL'))
    log('')
    log('  reminder: gfx_drv_clear/clear_all are gfx-driver table entries')
    log('  (%s indices 143/144, dump_gfx_table.py). They are called through'
        % 'gfx_drv_table.txt')
    log('  the driver struct, never by a direct B/BL, so a branch scan')
    log('  reports zero callers for them exactly as it does for gfx_drv_flip.')


def build_spec(install=True):
    patches = []
    for va, stock, new, what in WORDS:
        a, b = (stock, new) if install else (new, stock)
        patches.append({'name': what,
                        'va': '0x%X' % va,
                        'expect': _hex(a),
                        'set': _hex(b)})
    return {'name': 'clear colour -> black', 'patches': patches}


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


def apply(path, out, log=print, dry=False):
    m = nxmap.Main(path)
    st = state(m.text)
    if st == 'patched':
        log('  already installed; nothing to do')
        return True
    if st == 'unknown' or not verify_anchors(m.text, log):
        log('! this module is not the one the offsets were derived from; '
            'refusing to patch')
        return False
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
    """
    build.py's entry point. Same name and shape as ff7nx_widescreen's and
    ff7nx_fieldbg's, so it drops into the same chain of exefs/main passes.
    """
    return apply(src, dst, log=log)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='force the port frame clear colour to black')
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
