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
import ff7nx_daynight as D                                      # noqa: E402
import ff7nx_dispatch as DP                                     # noqa: E402
import ff7nx_voice as V                                         # noqa: E402
import ff7nx_ambient as AM                                      # noqa: E402
import a64 as A                                                 # noqa: E402

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
    'daynight': {
        # The Echo-S day/night cycle, at its two per-frame hooks.
        #
        # THIS FEATURE ONLY STARTED INSTALLING IN BUILD 355. It had been
        # failing with `NoRoom` for many builds -- the padding pool was full
        # -- and the facial leak fix freed 46 words, which was enough to let
        # it in. So a whole feature came back to life in the same build as
        # the newly reported camera jumps and the bugin1c black-screen hang.
        #
        # That is FINDINGS-304 s6 exactly: when a new symptom appears in the
        # same build as another change, revert the change first. It is one
        # word each and it cannot be the wrong thing to check.
        #
        # TICK is the per-frame tint driver on `field_draw_everything`;
        # CLEAR is the word after the gray-quad draw. Off means no tinting
        # and no clock, and nothing else in the build changes.
        'words': {D.TICK_HOOK: D.TICK_ORIG,
                  D.CLEAR_HOOK: D.CLEAR_ORIG},
        'on_says': 'the day/night cycle is active again.',
        'off_says': 'day/night is OFF -- no tint, no clock. If the camera '
                    'jumps or the hang go with it, that is the cause.',
    },
    'voice-field': {
        # The Echo-S voice runtime, FIELD SIDE ONLY.
        #
        #   FIELD_HOOK        the field voice service (builds/owns the player)
        #   MESSAGE_HOOK      the MESSAGE producer -- what asks for a line
        #   ASK_PRE_HOOK      the ASK window's producer
        #   MAPJUMP_HOOK      the map-change latch that retires a line
        #   NAME_CHANGE_HOOK  the rename window
        #
        # DELIBERATELY NOT INCLUDED: BATTLE_HOOK (0x8FB20) and
        # WORLD_SERVICE_HOOK (0xF1E0EC). `ff7nx_ambient` hooks THE SAME TWO
        # ADDRESSES, so restoring them would silently tear out half of the
        # ambient runtime as well and the result would mean nothing. Verified
        # against the built module before this switch was written.
        #
        # Off means no voice is requested or built in fields: dialogue is
        # silent, and the lip flap stops with it (the flap's third gate is a
        # live player). Everything else -- battle voice, ambient, the audio
        # worker -- is untouched.
        'words': {V.FIELD_HOOK: V.FIELD_ORIG,
                  V.MESSAGE_HOOK: V.MESSAGE_ORIG,
                  V.ASK_PRE_HOOK: V.ASK_PRE_ORIG,
                  V.MAPJUMP_HOOK: V.MAPJUMP_ORIG,
                  V.NAME_CHANGE_HOOK: V.NAME_CHANGE_ORIG},
        'on_says': 'the field voice runtime is active again.',
        'off_says': 'field dialogue is SILENT and mouths do not flap. If the '
                    'bugin1c hang clears, the voice runtime is holding the '
                    'script up.',
    },
    'ambient': {
        # Cosmo Memory's per-location ambience: the field tick that starts a
        # loop, and the two caves that dispose it on a menu or a mode change.
        #
        # WHY IT IS A NEW-GAME SUSPECT. A new game enters `md1stin` (field
        # 116), which HAS an ambience entry (1503, a 96 kHz loop), and on its
        # first main-loop tick Echo-S's script MAPJUMPs to `blackbgh` (field
        # 109), which has none. So the loop is started and disposed within a
        # frame or two of the title screen -- a start-then-stop no loaded
        # save ever performs. If the native player does not survive being
        # deleted while its worker is still opening the stream, that is an
        # instant crash before any text is drawn, which is what is reported.
        #
        # All three sites are owned by ff7nx_ambient alone (the battle and
        # world hooks are shared with the voice runtime and are NOT touched).
        # Off = no ambience anywhere; nothing else changes.
        'words': {AM.FIELD_HOOK: AM.FIELD_ORIG,
                  AM.MENU_HOOK: AM.MENU_ORIG,
                  AM.MODE_HOOK: AM.MODE_ORIG},
        'on_says': 'per-location ambience is active again.',
        'off_says': 'no field ambience is ever started. If a new game now '
                    'reaches the Echo-S welcome screen, the ambience start/'
                    'stop on md1stin is the crash.',
    },
    # The same three sites one at a time, so a single boot says WHICH.
    'ambient-field': {
        # The per-frame field tick: the only site that CREATES a player.
        'va': AM.FIELD_HOOK, 'on': None, 'off': AM.FIELD_ORIG,
        'on_says': 'the field ambience tick is active again.',
        'off_says': 'no field loop is ever started; the menu and mode stops '
                    'stay installed (and have nothing to stop).',
    },
    'ambient-menu': {
        # The menu-loop entry stop -- runs on the in-game menu, the title
        # hand-over and any field MENU opcode (Echo-S's naming screen).
        'va': AM.MENU_HOOK, 'on': None, 'off': AM.MENU_ORIG,
        'on_says': 'the ambient menu stop is active again.',
        'off_says': 'the menu-loop stop is gone; field loops still play and '
                    'the mode stop still disposes them.',
    },
    'ambient-mode': {
        # set_driver_mode's stop -- the BUILD 506 game-over fix, chained
        # behind ff7nx_daynight's hook two instructions earlier.
        'va': AM.MODE_HOOK, 'on': None, 'off': AM.MODE_ORIG,
        'on_says': 'the ambient mode stop (the game-over fix) is active again.',
        'off_says': 'the set_driver_mode stop is gone -- the game-over hang '
                    'can come back; field loops still play.',
    },
    'abort-hang': {
        # INSTRUMENT, not a fix. Starting an ambience loop on New Game closes
        # the software, file-independently. The engine has exactly three
        # places on that path where it deliberately calls exit(-1), each
        # after printing an [ASSERT] nobody can see:
        #
        #   +0x3150     MusicStream ctor   "music file can not be loaded"
        #   +0x36B4     MusicStream worker "music buffer can not be created"
        #   +0x1126A64  SoundBufferImpl    "g_WaveBufferAllocator alloc failed"
        #
        # ON turns each `bl exit` into `b .` -- the thread that hit it stops
        # there forever instead of taking the process down. The FIRST runs on
        # the main thread (inside the ambience cave), the other two on the
        # player's own worker thread, so the symptom names the site:
        #
        #   the picture FREEZES         -> +0x3150, the file would not open
        #   the game CARRIES ON         -> +0x36B4 / +0x1126A64, the worker
        #                                  could not get its sound buffer
        #   still "software was closed" -> none of these three; it is a
        #                                  system abort elsewhere
        #
        # A `b .` encodes to the same word at every address.
        'words': {0x3150: A.bl(0x3150, 0x1150EC0),
                  0x36B4: A.bl(0x36B4, 0x1150EC0),
                  0x1126A64: A.bl(0x1126A64, 0x1150EC0)},
        'on': 0x14000000,
        'on_says': 'the three audio exit(-1) sites now HANG instead. Freeze '
                   '= file open failed; game carries on = no sound buffer; '
                   'still closes = something else.',
        'off_says': 'the engine\'s own exit(-1) calls are back (stock).',
    },
    'abort-hang-file': {
        # abort-hang's ONE site that can be told apart by symptom.
        #
        # abort-hang froze the picture -- so it IS one of the three engine
        # exits, not a system abort. But its symptom table was wrong: the
        # worker hits +0x36B4 / +0x1126A64 WHILE HOLDING the player's lock
        # (taken at +0x3368, released at +0x3450), and the ambience cave
        # calls OGG_VOLUME -- which takes that same lock -- on every field
        # frame. So a hung worker freezes the main thread too, and all three
        # sites look identical.
        #
        # Two of the three are really one: SoundBufferImpl's create (+0x1126940)
        # returns 0 or exits at +0x1126A64 itself, so the worker's own null
        # check at +0x36B4 is unreachable. That leaves exactly two:
        #
        #   ON (this site hangs, the other stays exit):
        #     the picture FREEZES   -> the loop's FILE would not open (+0x3150)
        #     "software was closed" -> the 32 MB audio pool refused the
        #                              buffer (+0x1126A64)
        'va': 0x3150,
        'on': 0x14000000,
        'off': A.bl(0x3150, 0x1150EC0),
        'on_says': 'only the file-open exit (+0x3150) hangs now. Freeze = '
                   'the file would not open; closes = the audio pool.',
        'off_says': 'the file-open exit is back (stock).',
    },
    'moviepoll': {
        # The 30 fps FMV frame-counter halving, at BOTH sites.
        #
        # The mod's movies are re-encoded at 30 fps, so `ff7nx_60fps` halves
        # the movie frame counter and the MVIEF poll counter to keep scripted
        # cues on their original 15 fps frame numbers. Both do a plain
        # `lsr #1` -- floor.
        #
        # MEASURED over the shipped movies: the 30 fps encode produces 2n-1
        # frames, not 2n, so the halved counter tops out ONE SHORT of the
        # number the 15 fps original reached:
        #
        #     zmind01  135 -> 269 -> 134   (bugin1c, the observatory)
        #     zmind02  135 -> 269 -> 134
        #     zmind03  225 -> 449 -> 224
        #     southmk  242 -> 483 -> 241
        #
        # A script that waits for the movie's LAST frame therefore waits for
        # a number that never arrives. bugin1c polls MVIEF 24 times over the
        # zmind movies and hangs on a black screen with the music still
        # playing, which is that shape exactly.
        #
        # OFF restores both stock words: the counters run at the raw 30 fps
        # rate. Scripted cues keyed to frame numbers then fire EARLY (that is
        # the bug the halving exists to fix, and the log warns about it for
        # the opening), so this is a diagnostic and not a setting. If the
        # bugin1c hang goes away with it off, the halving is implicated and
        # the fix is to round up rather than down.
        #
        # THE ORIGINALS COME FROM `ff7nx_dispatch`, NOT FROM READING THE CAVE.
        # The first version of this switch guessed them by disassembling the
        # cave's first instruction, which is right for the poll site (the
        # cave replays the displaced `ldrh`) and WRONG for the frame site:
        # +0x42298 is `get_movie_frame`'s TAIL-CALL `b #0xA510`, and the
        # cave's first word is its own prologue. Writing that prologue back
        # left the stub falling through instead of tail-calling, and the game
        # crashed on save load. Declared constants, never inference.
        'words': {DP.MVIEF_POLL_HOOK: DP.MVIEF_POLL_DISPLACED,
                  DP.MOVIE_FRAME_TAILCALL: A.b(DP.MOVIE_FRAME_TAILCALL,
                                               DP.MOVIE_FRAME_DISPATCH)},
        'on_says': 'the 30 fps counter halving is active again.',
        'off_says': 'movie counters run raw. Cues keyed to frame numbers '
                    'fire early -- diagnostic only. If the bugin1c hang '
                    'clears, the halving is the cause.',
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
