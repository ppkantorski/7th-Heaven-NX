#!/usr/bin/env python3
r"""
initclamp_toggle.py -- turn the SCR2D initializer cave off and on IN PLACE.

    python3 initclamp_toggle.py <main> --show
    python3 initclamp_toggle.py <main> --off     # cave never executes
    python3 initclamp_toggle.py <main> --on      # back to the built state

ONE WORD. No rebuild, no archive, seconds either way.

WHY
===
`ff7nx_camclamp` installs two caves. The ACTIVE one (the per-frame path at
+0x9F874C) has shipped for many builds. The SCR2D INITIALIZER one
(+0x9F7AF4, inside `field_init_scripted_bg_movement`) is new, and it is the
newest code in the module:

    log 350   its first version corrupted x20 and crashed scene transitions
    log 351   x20 fixed; "battle enters super slowly, then freezes"
    log 352   battle entered with corrupted UI textures

Build 511 is the interesting one: the cave was installed but its gate was
never true, so it never clamped -- and no battle problem was reported.

That is a correlation, not a cause, and a halfword written to
`field_curr_delta_world_pos` has no obvious route to a corrupt texture. But
FINDINGS-304 §6 is explicit about what to do here:

    when a new symptom appears in the same build as a speculative change,
    revert the speculative change first -- before investigating the symptom.
    Not because it is likely, but because it is cheap, and because
    everything measured while it is still in place is measured through it.

This is that revert, made cheap enough to do between two test runs.

WHAT IT DOES
============
`INIT_HOOK_VA` holds `b #0x9F7C78` in the stock module -- the branch that
ends case 4 of the switch. `--apply` replaces it with a branch to the cave.
This flips that one word back and forth:

    --off   restore the stock branch. The cave's 48 words stay in the
            padding, unreferenced and unexecuted. The ACTIVE cave and the
            early-out trampoline are UNTOUCHED, so the module goes to
            exactly the build-511 camera behaviour: FMVs correct, 7th
            Heaven snap back.
    --on    restore the branch to the cave.

The displaced word is kept in `<main>.initclamp` beside the module, so
`--on` restores the exact branch that was there rather than recomputing it.

WHAT THE ANSWER MEANS
=====================
    corruption GONE with --off   -> it is this cave. Do not ship it again
                                    until the mechanism is understood; the
                                    camera fix is not worth a corrupt battle.
    corruption STILL THERE       -> it is not the camera at all, and every
                                    measurement taken from here is clean.
                                    Next suspects, in order: the Cosmo
                                    Memory `Base` layer (enabled ~506, adds
                                    battle textures and sfx), the 64->256 MB
                                    guest heap, the battle texture caps.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import ff7nx_camclamp as C                                      # noqa: E402


def read_word(path, va):
    return C.w32(C._text(path), va)


def write_word(path, va, word, why):
    """
    Patch one word through `nso_patcher`, the same path `--apply` uses.

    Not a raw file seek: the module is an NSO whose .text may be compressed,
    so a file offset computed by hand is wrong in a way that only shows up on
    hardware. `apply_spec` also verifies the word it is replacing, so a
    module that is not in the state this tool thinks it is in is refused
    rather than corrupted.
    """
    import nso_patcher
    from pathlib import Path
    p = Path(path)
    nso = nso_patcher.read_nso(p)
    spec = {'name': 'initclamp_toggle',
            'patches': [{'name': why,
                         'va': '0x%X' % va,
                         'expect': C._fmt(read_word(path, va)),
                         'set': C._fmt(word)}]}
    nso_patcher.apply_spec(nso, spec)
    p.write_bytes(nso_patcher.rebuild(nso))


def state(path):
    w = read_word(path, C.INIT_HOOK_VA)
    if w == C.INIT_HOOK_ORIG:
        return 'off', None
    tgt = C._b_target(w, C.INIT_HOOK_VA)
    return ('on', tgt) if tgt is not None else ('unknown', None)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('main')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--show', action='store_true')
    g.add_argument('--off', action='store_true')
    g.add_argument('--on', action='store_true')
    a = ap.parse_args(argv)

    side = a.main + '.initclamp'
    st, tgt = state(a.main)

    if a.show:
        print('  %s' % a.main)
        print('    +%#x  %s%s' % (C.INIT_HOOK_VA, st,
                                  '' if tgt is None else
                                  '  -> cave %#x' % tgt))
        n = C.init_cave_length(C._text(a.main))
        print('    initializer cave in padding: %s word(s)'
              % (n if n else 'none found'))
        print('    active cave: %s word(s)  (untouched by this tool)'
              % C.cave_length(C._text(a.main)))
        if os.path.exists(side):
            print('    saved branch: %s' % open(side).read().strip())
        return 0

    if a.off:
        if st == 'off':
            print('  already off -- nothing to do')
            return 0
        if st != 'on':
            print('  ! +%#x is neither the stock branch nor one this module '
                  'wrote; refusing to guess' % C.INIT_HOOK_VA)
            return 1
        with open(side, 'w') as f:
            f.write('%#010x\n' % read_word(a.main, C.INIT_HOOK_VA))
        write_word(a.main, C.INIT_HOOK_VA, C.INIT_HOOK_ORIG,
                   'initializer hook -> stock branch')
        back, _ = state(a.main)
        if back != 'off':
            print('  ! the write did not take -- do not boot this')
            return 1
        print('  SCR2D initializer cave DISABLED (one word at +%#x).'
              % C.INIT_HOOK_VA)
        print('  The active cave and the early-out trampoline are untouched:')
        print('    FMV camera  correct')
        print('    7th Heaven  the snap is back -- expected, that is the test')
        print('  Re-run with --on to put it back.')
        return 0

    if st == 'on':
        print('  already on -- nothing to do')
        return 0
    if not os.path.exists(side):
        print('  ! no saved branch beside the module; run '
              'ff7nx_camclamp.py %s --apply instead' % a.main)
        return 1
    word = int(open(side).read().strip(), 16)
    write_word(a.main, C.INIT_HOOK_VA, word,
               'initializer hook -> cave')
    back, tgt = state(a.main)
    if back != 'on':
        print('  ! the write did not take -- do not boot this')
        return 1
    print('  SCR2D initializer cave RE-ENABLED -> cave %#x' % tgt)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
