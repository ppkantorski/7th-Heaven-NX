#!/usr/bin/env python3
r"""
ff7nx_bglayers.py -- DIAGNOSTIC. Which battleground layers does the battle
scene hide while a floor-warp effect runs?

THIS IS AN EXPERIMENT, NOT A FIX. It changes one word, it is reversible, and
its purpose is to answer a question the last seven builds could not.

WHAT THE TV SAID, AND WHY IT POINTS HERE
========================================
> "it happens on the grass area, where fx typically only occupies the LOWEST
>  LAYER everyone stands upon"
> "the edge of the snapshot has black squares on it"
> "the circular warp effect is being clipped / cut off on the sides parallel
>  to battle"
> "this doesnt happen on flatter terrain like the beach"

The FF7 battlefield is not one surface. `update_3d_battleground` (x86
0x42E52E) draws several separate models, and they live at different offsets
off one base:

    0x42E5F2   0x42F1AD(1, [0xBE1128] + 0x0070, 0xC, 0)
    0x42E609   0x42F1AD(5, [0xBE1128] + 0x0070, 0xC, 0)
    0x42E621   0x42F1AD(6, [0xBE1128] + 0x0070, 0xC, 0)
    0x42E7DE   0x42F1AD(1, [0xBE1128] + 0x4070, 0, 0)     <-- a DIFFERENT model

and three of those draws are gated on the low three bits of a single byte:

    0x42E670   and edx, 1   -> skip                (bit 0)
    0x42E682   and eax, 4   -> take another branch (bit 2)
    0x42E7C6   and ecx, 2   -> skip                (bit 1)

The battle scene script sets all three at once, at one tick, and clears them
at the end of the sequence:

    x86 0x43B02A   cmp edx, 0x13
    x86 0x43B034   or  al, 7          <-- HIDE LAYERS 0, 1 AND 2
    x86 0x43CBE6   and cl, 0xF8       <-- show them again
    ARM +0x109E3C  orr w22, w8, #7

**So during these effects the game deliberately hides battleground layers and
lets the effect stand in for them.** If the effect only reaches the layer the
party stands on, whatever the other layers covered is hidden with nothing
drawn in its place -- and nothing drawn is BLACK.

That is the first mechanism in this whole investigation that:

    * produces black rather than "the wrong picture"   (a hidden layer)
    * follows the terrain's own contour                (it is a layer seam)
    * runs along the stage's long axis                 (where the seams are)
    * is welded to the floor through a camera rotate   (layers are floor)
    * is absent on a flat, single-layer stage          (nothing left over)
    * and is exactly what the TV described unprompted, in their own words

THE EXPERIMENT
==============
`SEVENTH_NX_BG_LAYERS` sets the mask that word ORs in. 7 is stock.

    7   stock -- hide layers 0, 1 and 2
    3   keep layer 2 visible
    1   keep layers 1 and 2 visible
    0   hide nothing at all                      <-- the default here

Mask 0 is the strongest signal: if the black regions fill in with ordinary
battlefield instead of black, a hidden-and-unreplaced layer IS the mechanism
and the next build bisects which bit. If nothing changes, the hide is not
involved and this entire branch is closed.

Expect other battle transitions that rely on the hide (screen darkenings
between phases) to look different with mask 0. That is the cost of a clean
signal and it is why this is labelled a diagnostic.

WHAT IT DOES NOT DO
===================
It does not touch the capture, the effects' own geometry, depth, culling,
UVs, or any summon's code. One word in the battle scene script.
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

import nxmap                                                   # noqa: E402

LAYERS_ENV = 'SEVENTH_NX_BG_LAYERS'

HOOK = 0x109E3C
STOCK = 0x32000916                 # orr w22, w8, #7

# mask -> the word that produces it. ORR (immediate) can encode a contiguous
# run of low bits; 0 is not a legal ORR immediate, so it becomes the move.
#   ORR Wd, Wn, #imm : sf=0 opc=01 100100 N=0 immr=0 imms=count-1
WORDS = {
    7: 0x32000916,                 # orr w22, w8, #7      (stock)
    3: 0x32000516,                 # orr w22, w8, #3
    1: 0x32000116,                 # orr w22, w8, #1
    0: 0x2A0803F6,                 # mov w22, w8          (hide nothing)
}

# The instructions around it, so the build refuses rather than writing into
# the wrong routine if a game update moves things.
ANCHORS = {
    0x109E34: 0x39400008,          # ldrb w8, [x0]     the flag byte, read
    0x109E38: 0x2A1503E0,          # mov  w0, w21
    0x109E40: 0x390002F6,          # strb w22, [x23]   written back
    0x109E48: 0x39000016,          # strb w22, [x0]    and to the guest global
}

DEFAULT_MASK = 0


def mask() -> int:
    v = os.environ.get(LAYERS_ENV)
    if v is None:
        return DEFAULT_MASK
    try:
        m = int(v, 0)
    except ValueError:
        return DEFAULT_MASK
    return m if m in WORDS else DEFAULT_MASK


def enabled() -> bool:
    """ON whenever the requested mask is not the stock 7."""
    return mask() != 7


def _word(img, va):
    return struct.unpack_from('<I', img, va)[0]


def verify(img) -> list:
    bad = []
    for va, want in sorted(ANCHORS.items()):
        got = _word(img, va)
        if got != want:
            bad.append('anchor +0x%X is %08X, expected %08X' % (va, got, want))
    got = _word(img, HOOK)
    if got not in WORDS.values():
        bad.append('+0x%X is %08X, which is none of the four masks' % (HOOK, got))
    return bad


def read_state(img) -> str:
    got = _word(img, HOOK)
    for m, w in WORDS.items():
        if w == got:
            return 'mask %d' % m
    return 'unknown'


def plan(m_, revert=False):
    img = m_.img
    problems = verify(img)
    if problems:
        return [], [], problems
    want = WORDS[7 if revert else mask()]
    have = _word(img, HOOK)
    if have == want:
        return [], [], []
    name = ('restore stock layer hide (mask 7)' if revert
            else 'battleground layer hide mask %d' % mask())
    return ([{'name': name, 'va': hex(HOOK),
              'expect': struct.pack('<I', have).hex(),
              'set': struct.pack('<I', want).hex()}],
            ['    %s' % name], [])


def _write(main, patches, log):
    import nso_patcher
    main = Path(main)
    nso = nso_patcher.read_nso(main)
    for line in nso_patcher.apply_spec(
            nso, {'name': 'battleground layer hide', 'patches': patches}):
        log('    ' + line)
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.bglayers-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(nso_patcher.rebuild(nso))
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def apply_all(main, revert=False, log=print):
    m_ = nxmap.Main(str(main))
    patches, notes, problems = plan(m_, revert=revert)
    if problems:
        for p in problems:
            log('  ! ' + p)
        log('  refusing to write the battleground layer hide.')
        return 1
    log('  battleground layer hide (DIAGNOSTIC, %s=%d, stock is 7):'
        % (LAYERS_ENV, 7 if revert else mask()))
    for n in notes:
        log(n)
    if not patches:
        log('    nothing to do -- already in the requested state')
        return 0
    _write(main, patches, log)
    log('  %d layer-hide word(s) written' % len(patches))
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('main')
    ap.add_argument('--revert', action='store_true')
    a = ap.parse_args()
    raise SystemExit(apply_all(a.main, revert=a.revert))
