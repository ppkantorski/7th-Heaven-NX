#!/usr/bin/env python3
"""
test_initclamp.py -- when the SCR2D one-shot clamp must NOT run.

    PYTHONPATH=. python3 tests/test_moviegate.py

The SCR2D initializer clamp (case 4 of `field_init_scripted_bg_movement`)
fixes an abrupt horizontal snap: the one-shot camera placement was left
unclamped while the following smooth move was clamped, so changing camera
mode jumped -- +/-162 px when Barret comes through the door in `mds7pb_1`,
+/-54 px on the chair.

Run unconditionally it breaks the FMVs, because `field_curr_delta_world_pos`
is ALSO what the movie camera anchors on. This file pins the condition that
separates the two, and the two wrong answers that came before it.

Three answers were tried. The first two are recorded so they are not
tried again.

WRONG 1 -- `is_fieldmap_wide()` as a per-field data byte (build 511).
    It reads like a per-field property and is not: `getMode()` returns
    WM_DISABLED whenever `driver_mode != MODE_FIELD`, so it is a runtime
    test. And southmk2 resolves to WM_EXTEND_ONLY, not disabled, so the
    flag would have been 1 and the clamp would have fired anyway. The
    belief that it was disabled came from `field_camera_ranges.csv`, a
    stale August artifact.

WRONG 2 -- gate on the movie flags, word_CC1638 / BGMOVIE / MVCAM
    (build 512). The right shape, the wrong moment: at the time case 4
    runs no movie has started, so word_CC1638 is 0 and the gate never
    fired. mds7pb_1 was fixed, southmk2 post-Airbuster still lost its
    black bar.

RIGHT -- the clamp's own bounds. `southmk2`'s shipped camera range is
    -160..160, so the bounds are `[left+160, right-160]` = `[0, 0]`: the
    clamp admits exactly one value. That is not clamping, it is replacing
    the authored `SCR2D (+32, -32)` with a constant. `mds7pb_1` is
    -202..202 -> `[-42, 42]`, real travel, a real clamp.

    MEASURED over the shipped archive: 394 of 711 fields have bounds that
    meet, 317 have travel, and ZERO have `lo > hi` -- so the old `b.gt`
    guard was dead code. `b.ge` costs no words and makes it live.

    The ACTIVE per-frame cave keeps `b.gt` and is untouched, which is what
    still centres southmk2 before the Airbuster battle.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import ff7nx_camclamp as C                                      # noqa: E402

FAIL = []


def ck(cond, what):
    print('  %s  %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        FAIL.append(what)


# MEASURED out of the SHIPPED archive, not out of a table.
#   southmk2 -160..160 -> the clamp admits exactly [0, 0]: it centres.
#   mds7pb_1 -202..202 -> admits [-42, 42], which is ChatGPT's own measured
#                         "-96 -> -42, +64 -> +42".
SOUTHMK2 = (-160, 160)
MDS7PB_1 = (-202, 202)
AUTHORED = 32           # southmk2's setup executes SCR2D (+32, -32)


def main():
    print('southmk2 -- ZERO TRAVEL, SO THE CLAMP IS A CONSTANT')
    print('  shipped -160..160 -> bounds [0, 0]. Anything it touches')
    print('  becomes 0, including the authored SCR2D (+32, -32).')
    for dx in (AUTHORED, -AUTHORED, 96, -162, 162):
        got = C.emulate_init(dx, *SOUTHMK2, vclip=0)
        ck(got['x'] == dx,
           'SCR2D %5d stays %5d -- the post-Airbuster black bar survives'
           % (dx, got['x']))
    lo, hi = SOUTHMK2[0] + 160, SOUTHMK2[1] - 160
    ck(lo == hi == 0, 'its bounds really do meet, at %d' % lo)

    print('')
    print('mds7pb_1 -- REAL TRAVEL, SO THE CLAMP IS A CLAMP')
    for dx, want in ((-96, -42), (64, 42), (-162, -42), (162, 42)):
        got = C.emulate_init(dx, *MDS7PB_1, vclip=0)
        ck(got['x'] == want,
           'SCR2D %5d -> %4d, the bounds the active path uses' % (dx, got['x']))
    for dx in (-42, -40, 0, 40, 42):
        got = C.emulate_init(dx, *MDS7PB_1, vclip=0)
        ck(got['x'] == dx, 'a legal position is a fixed point: %5d' % dx)
    for dx in (-96, 64, -162):
        init = C.emulate_init(dx, *MDS7PB_1, vclip=0)
        active = C.emulate(dx, *MDS7PB_1, vclip=0)
        ck(init['x'] == active['x'],
           'one-shot and active agree at %5d (%4d) -- a disagreement IS the '
           'snap Barret\'s entrance shows' % (dx, init['x']))

    print('')
    print('THE GUARD IS ONE CONDITION CODE, IN ONE CAVE')
    try:
        from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
        import struct
        md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)

        def mn(words):
            blob = b''.join(struct.pack('<I', w) for w in words)
            return [i.mnemonic for i in md.disasm(blob, 0x2000)]

        n = C.init_n_words(True)
        init = mn(C.init_cave_words(lambda i: 0x2000 + 4 * i, 0x2000 + 4 * n))
        n2 = C.n_words(True)
        act = mn(C.cave_words(lambda i: 0x2000 + 4 * i, 0x2000 + 4 * n2, True))
        ck(init.count('b.ge') == 2 and init.count('b.gt') == 0,
           'the INIT cave uses b.ge on both legs -- zero travel is skipped')
        ck(act.count('b.gt') == 2 and act.count('b.ge') == 0,
           'the ACTIVE cave still uses b.gt and is UNTOUCHED -- it is what '
           'centres southmk2 before the battle, and changing it is what the '
           'v5 note warns about')
    except ImportError:
        print('  (capstone absent -- condition codes not checked)')
    ck(C.init_n_words(False) == 24 and C.init_n_words(True) == 48,
       'the guard costs no words: 24 / 48, as before')

    print('')
    print('BOTH LEGS, AND THE ELEVATOR')
    got = C.emulate_init(AUTHORED, *SOUTHMK2, delta_y=-32, top=-120,
                         bottom=120, vclip=1)
    ck(got['x'] == AUTHORED and got['y'] == -32,
       'a zero-travel y is left alone too -- the freeze ff7nx_vclip '
       'documents, on the axis it documents it for')

    print('')
    print('REGISTERS AND RETURN')
    for lbl, rng in (('skipped', SOUTHMK2), ('clamped', MDS7PB_1)):
        g = C.emulate_init(-162, *rng, vclip=0)
        ck(g['x19'] == C.WORLD_MOVE_STATUS,
           '%s: x19 survives -- the return point does `mov w0, w19`' % lbl)
        ck(g['x20'] == 0x2400000,
           '%s: x20, the live guest-CPU context, survives (log 350)' % lbl)
        ck(g['return'] == 0x4000000 + 4 * C.init_n_words(True),
           '%s: the continuation is rejoined' % lbl)

    print('')
    print('EVERY EARLIER CAVE STAYS REVERTABLE')
    ck(C.INIT_LEGACY_LENGTHS == (60, 50, 36, 26),
       'the movie-gate (36/60) and wide-flag (26/50) caves are remembered')
    ck(C.INIT_KNOWN_LENGTHS[0] == C.init_n_words(True),
       'the current length is probed first')
    ck(len(set(C.INIT_KNOWN_LENGTHS)) == len(C.INIT_KNOWN_LENGTHS),
       'no length is listed twice')

    print('')
    print('%d failure(s)' % len(FAIL) if FAIL else 'all checks passed')
    return 1 if FAIL else 0


if __name__ == '__main__':
    raise SystemExit(main())
