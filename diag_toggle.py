#!/usr/bin/env python3
r"""
diag_toggle.py -- flip one word in a BUILT main, for A/B tests on hardware.

    python3 diag_toggle.py <main> --show
    python3 diag_toggle.py <main> heapabort --on
    python3 diag_toggle.py <main> heapabort --off
    python3 diag_toggle.py <main> initclamp --off

No rebuild, no archive, seconds either way. Writes through `nso_patcher`
(the same verified path `--apply` uses), which checks the word it is
replacing, so a module that is not in the state this tool expects is refused
rather than corrupted. Supersedes `initclamp_toggle.py`.

=============================================================================
heapabort -- STOP LOSING THE EVIDENCE
=============================================================================
`ff7nx_heap` raises the FF7 guest heap 64 -> 256 MB, and as part of that it
NOPs one word on the allocation-failure path. Shipped, that path reads:

    +0x10EE8CC  cbnz w19, #0x10EE894    keep walking the free list
    +0x10EE8D0  mov  w0, w20            the heap descriptor
    +0x10EE8D4  nop                     <- was `bl #0x10EE660`, the abort
    +0x10EE8D8  mov  w19, wzr           return NULL
    +0x10EE8DC  mov  w0, w19

So **when the guest heap is exhausted, HeapAlloc silently returns NULL and
the game carries on with a null pointer.** Whatever was going to be loaded
into that block is then read from whatever is actually at that address.

That is exactly the shape of the reported fault: play a long field sequence,
exit to the title, load a second save, enter a battle, and the battle UI
draws from garbage -- with no crash and no log. Booting straight into the
second save is clean, which is what a leak across the first session looks
like.

`--on` restores the `bl`. It changes NOTHING while allocations succeed; it
only fires when one fails, and then it aborts AT the failing allocation and
dumps the heap, so the crash log names it. It cannot make a working build
worse -- it can only turn a silent corruption into a report.

    aborts, with a log   -> the guest heap IS being exhausted. The log says
                            where, and the leak hunt has a target.
    corrupts as before,
    no abort             -> the heap is NOT exhausted. It is a stale cache
                            or a texture/model table that survives the exit
                            to title, and the heap is cleared as a suspect.

Leave it on for the whole test. Set it back to `--off` before you ship, or
just rebuild -- a normal build re-applies the NOP.

=============================================================================
initclamp -- the SCR2D initializer cave
=============================================================================
Already answered (it is not the camera), kept so the A/B can be repeated.
`--off` restores the stock branch at the hook; the cave's words stay in the
padding, unreferenced. The active cave and the early-out trampoline are not
touched, so the camera goes to build-511 behaviour: FMVs correct, the 7th
Heaven snap back.
"""
from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import ff7nx_camclamp as C                                      # noqa: E402
import ff7nx_facial as F                                        # noqa: E402

NOP = 0xD503201F

SWITCHES = {
    'facial': {
        # The advanced facial-animation RUNTIME, all five hooks at once.
        # Off = the stock code runs and the extra flevel textures simply go
        # unreferenced, which is what the game did before build 461.
        #
        # This is here because the leak accumulates with FIELDS WALKED, and
        # this runtime allocates PER MODEL PER FIELD -- eye art left and
        # right, and a mouth texture -- giving them back in
        # `build_free_cave`, which is hooked on the NEXT field's model
        # teardown loop, bounded by MAX_MODELS and gated on a per-slot flag.
        # A field whose models fall outside that bound or that flag leaks
        # every texture it allocated, and the reported route
        # (mds7plr1 -> mds7 -> mds7pb_1) is exactly a crowd of models.
        'words': {F.BLINK_HOOK: F.BLINK_ORIG,
                  F.MOUTH_HOOK: F.MOUTH_ORIG,
                  F.KAWAI_HOOK: F.KAWAI_ORIG,
                  F.FREE_HOOK: F.FREE_ORIG,
                  F.SPEAK_HOOK: F.SPEAK_ORIG},
        'on_says': 'the advanced facial-animation runtime is active again.',
        'off_says': 'the facial runtime is OFF (stock blink/mouth). Faces '
                    'lose the advanced animation; nothing else changes. If '
                    'the corruption goes with it, this is the leak.',
    },
    'facial-blink': {
        # The blink/eye hook (x86 0x649B50 field_blink_3d_model). This is the
        # chain that captures the stock pointers into the slot AND loads the
        # eye art -- build_load_cave is branched to from here. Off means no
        # eye texture is ever loaded and no slot is ever populated; the mouth
        # hook, KAWAI and the free cave stay installed.
        #
        # SPEAK is already eliminated, so if the leak is in facial it is here
        # or in KAWAI. This is the bigger half.
        'va': F.BLINK_HOOK,
        'on': None,
        'off': F.BLINK_ORIG,
        'on_says': 'blink and eye-art loading are active again.',
        'off_says': 'no eye art is loaded and no slot is populated. Stock '
                    'blink behaviour.',
    },
    'facial-kawai': {
        # The KAWAI script opcode (x86 0x620136) -- authored expression and
        # EYETX/mouth indices. Off means scripted expression changes never
        # reach the slot, so the eye art is never RE-loaded for a new index.
        'va': F.KAWAI_HOOK,
        'on': None,
        'off': F.KAWAI_ORIG,
        'on_says': 'scripted KAWAI expressions are active again.',
        'off_says': 'scripted expression changes are ignored, so eye art is '
                    'never reloaded for a new index.',
    },
    'facial-mouth': {
        # The hook that applies our mouth texture to the renderer
        # (hundred_data_group_array[3]). NOTE: the mouth LOADER is reached
        # from the blink chain, so this does not stop mouth loading -- it
        # stops it being drawn. Useful to separate "loaded" from "applied".
        'va': F.MOUTH_HOOK,
        'on': None,
        'off': F.MOUTH_ORIG,
        'on_says': 'the mouth texture is applied to the renderer again.',
        'off_says': 'the mouth texture is no longer applied. It is still '
                    'LOADED -- the loader hangs off the blink chain.',
    },
    'facial-speak': {
        # ONE word: the MESSAGE-opcode hook that records which model opened
        # the dialogue window. Without it `HDR_SPEAKER` stays 0, so the
        # "this model is speaking, flap its lip" clause never fires and the
        # VOICE-DRIVEN mouth textures are never loaded. A KAWAI EYETX index
        # still loads a mouth, and eyes/blink are untouched.
        #
        # This is the intersection of the facial runtime and the recent
        # Echo-S voice work, and the reported route is dialogue-heavy. If
        # `facial --off` fixes it and this does too, the leak is in the
        # voice-driven mouth path specifically, not in facial as a whole.
        'va': F.SPEAK_HOOK,
        'on': None,
        'off': F.SPEAK_ORIG,
        'on_says': 'voice-driven lip flap is active again.',
        'off_says': 'the speaker is never recorded, so voice-driven mouth '
                    'textures are never loaded. Eyes, blink and KAWAI-driven '
                    'mouths still work.',
    },
    'heapabort': {
        'va': 0x10EE8D4,
        # bl #0x10EE660 -- the heap dump + abort on HeapAlloc failure.
        'on': 0x97FFFF63,
        'off': NOP,
        'on_says': 'HeapAlloc failure now ABORTS and dumps the heap. '
                   'Nothing changes while allocations succeed.',
        'off_says': 'HeapAlloc failure is silent again (returns NULL). '
                    'This is what a normal build ships.',
    },
    'initclamp': {
        'va': C.INIT_HOOK_VA,
        'on': None,                      # a branch to the cave; see sidecar
        'off': C.INIT_HOOK_ORIG,
        'on_says': 'the SCR2D initializer cave runs again.',
        'off_says': 'the SCR2D initializer cave never executes. The active '
                    'cave and trampoline are untouched: FMVs correct, the '
                    '7th Heaven snap is back -- that is how you know it took.',
    },
}


def read_word(path, va):
    return C.w32(C._text(path), va)


def write_word(path, va, word, why):
    import nso_patcher
    from pathlib import Path
    p = Path(path)
    cur = read_word(path, va)
    if cur == word:
        return False
    nso = nso_patcher.read_nso(p)
    nso_patcher.apply_spec(nso, {
        'name': 'diag_toggle',
        'patches': [{'name': why, 'va': '0x%X' % va,
                     'expect': C._fmt(cur), 'set': C._fmt(word)}]})
    p.write_bytes(nso_patcher.rebuild(nso))
    return True


def off_words(sw):
    """{va: the word that means OFF} for a switch, single or multi."""
    if 'words' in sw:
        return dict(sw['words'])
    return {sw['va']: sw['off']}


def state_of(path, name):
    sw = SWITCHES[name]
    off = off_words(sw)
    cur = {va: read_word(path, va) for va in off}
    if cur == off:
        return 'off'
    if 'words' in sw:
        # Multi-word: ON means every site differs from its stock word. A
        # mixture is not a state this tool produced, and is reported rather
        # than silently treated as one.
        if all(cur[va] != off[va] for va in off):
            return 'on'
        n = sum(1 for va in off if cur[va] == off[va])
        return 'unknown(%d of %d at stock)' % (n, len(off))
    w = cur[sw['va']]
    if sw.get('on') is not None and w == sw['on']:
        return 'on'
    if sw.get('on') is None and C._b_target(w, sw['va']) is not None:
        return 'on'
    return 'unknown(%s)' % C._fmt(w)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('main')
    ap.add_argument('switch', nargs='?', choices=sorted(SWITCHES))
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--show', action='store_true')
    g.add_argument('--on', action='store_true')
    g.add_argument('--off', action='store_true')
    a = ap.parse_args(argv)

    if a.show or not a.switch:
        print('  %s' % a.main)
        for name in sorted(SWITCHES):
            sw = SWITCHES[name]
            where = ('%d site(s)' % len(sw['words'])) if 'words' in sw \
                else '+%#x' % sw['va']
            print('    %-10s %-22s  (%s)'
                  % (name, state_of(a.main, name), where))
        return 0

    sw = SWITCHES[a.switch]
    side = '%s.%s' % (a.main, a.switch)
    st = state_of(a.main, a.switch)

    if not (a.on or a.off):
        print('    %s is %s' % (a.switch, st))
        return 0

    want = 'on' if a.on else 'off'
    if st == want:
        print('  %s is already %s -- nothing to do' % (a.switch, want))
        return 0
    if st.startswith('unknown'):
        print('  ! +%#x holds %s, which is neither state this tool knows; '
              'refusing to guess' % (sw['va'], st))
        return 1

    off = off_words(sw)
    if want == 'off':
        # Save what is there now so --on can put back the exact words,
        # including branch targets this tool cannot recompute.
        with open(side, 'w') as f:
            for va in sorted(off):
                f.write('%#x %#010x\n' % (va, read_word(a.main, va)))
        target = off
    else:
        static = {va: sw['on'] for va in off} if sw.get('on') is not None \
            else None
        if static is not None:
            target = static
        else:
            if not os.path.exists(side):
                print('  ! no saved words beside the module; rebuild, or run '
                      'the owning module\'s --apply')
                return 1
            target = {}
            for line in open(side):
                va, word = line.split()
                target[int(va, 16)] = int(word, 16)
            if set(target) != set(off):
                print('  ! the saved words do not cover the same sites; '
                      'refusing')
                return 1

    for va in sorted(target):
        write_word(a.main, va, target[va], '%s -> %s' % (a.switch, want))
    back = state_of(a.main, a.switch)
    if back != want:
        print('  ! the write did not take (now %s) -- do not boot this' % back)
        return 1
    print('  %s -> %s   (%d site(s))' % (a.switch, want, len(target)))
    print('  %s' % sw['%s_says' % want])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
