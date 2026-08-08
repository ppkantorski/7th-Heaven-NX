#!/usr/bin/env python3
"""
test_smooth_gate_compose.py -- the wrapper and the tick gate, together.

THE BUG THIS EXISTS FOR
=======================
Both of these make scripted movement advance once per field tick PAIR, and
they do it in different places:

  * the tick gate lives INSIDE `field_update_single_model_position`. It keeps
    the step on an even tick and zeroes it on an odd one. Correct when every
    frame calls the function.

  * the smooth wrapper wraps the CALL. It calls once per pair and replays the
    answer on the other frame.

Ship both and they compose catastrophically. The wrapper's single call lands
on whichever tick parity the model happened to start moving on; if that is the
odd one, the gate zeroes every step the call ever computes. The model never
moves, the arrival test never fires, and the scene never ends -- Cloud walks
on the spot until the game is killed. Fifty-fifty on when the move began,
which is why some cutscenes looked right and the weapon-shop bedroom hung.

Neither component is wrong on its own, and neither component's own tests can
see it: this is a bug that exists only in the composition. So it is tested as
a composition -- the gate's real emitted words are inspected for which
behaviour they encode, and the pair is simulated over both starting parities.
"""
import struct
import sys

import capstone

import ff7nx_60fps as F

CAVE = 0x115269C
TICK = 0x3FEE328
FLAG = 0x3FEE32C


def gate_word12(smooth):
    w = F._walk_gate_cave(28, F.WALK_X_ORIG, F.WALK_X_RAW_RECOVER,
                          CAVE, TICK, FLAG, F.WALK_X_HOOK, smooth)
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    return list(md.disasm(struct.pack('<I', w[12]), CAVE + 48))[0]


def simulate(gate_zeroes_odd, wrapper, start_tick, frames=12):
    """
    Steps applied over `frames` field frames.

    `gate_zeroes_odd` -- the gate is present (scripted step zeroed on odd tick)
    `wrapper`         -- the smooth wrapper calls once per pair
    """
    pos = 0
    phase = 0
    for i in range(frames):
        tick = start_tick + i
        if wrapper and phase == 1:
            phase = 0            # replay frame: no call at all
            continue
        step = 0 if (gate_zeroes_odd and (tick & 1)) else 1
        pos += step
        if wrapper:
            phase = 1
    return pos


def main():
    bad = 0

    # ---- what the emitted gate word actually encodes ---------------------
    off = gate_word12(False)
    on = gate_word12(True)
    if off.mnemonic != 'tbz':
        print('FAIL  with the wrapper OFF the gate must still be the tick '
              'gate, got %s %s' % (off.mnemonic, off.op_str))
        bad += 1
    else:
        print('  ok  wrapper off: the scripted path is still tick-gated')
    if on.mnemonic != 'b':
        print('FAIL  with the wrapper ON the gate must pass the full step '
              'through, got %s %s' % (on.mnemonic, on.op_str))
        bad += 1
    else:
        print('  ok  wrapper on:  the scripted path passes the step through')

    # ---- the composition, from BOTH starting parities --------------------
    # what shipped before the wrapper existed: gate only, one step per pair
    base = [simulate(True, False, t) for t in (0, 1)]
    if base != [6, 6]:
        print('FAIL  the gate alone should give one step per pair, got %r'
              % base)
        bad += 1
    else:
        print('  ok  gate alone: 6 steps in 12 frames, either parity')

    # the bug: gate AND wrapper. One parity halves, the other DEAD STOPS.
    broken = [simulate(True, True, t) for t in (0, 1)]
    if 0 not in broken:
        print('FAIL  this test no longer reproduces the freeze it exists for '
              '-- got %r, expected one parity to be 0' % broken)
        bad += 1
    else:
        print('  ok  gate+wrapper together reproduces the freeze (%r) -- '
              'which is why it is not what ships' % broken)

    # what ships: wrapper only, and it must be one step per pair on EITHER
    # parity. This is the assertion the shipped build rests on.
    fixed = [simulate(False, True, t) for t in (0, 1)]
    if fixed != [6, 6]:
        print('FAIL  wrapper with the gate passing through must give one step '
              'per pair on either parity, got %r' % fixed)
        bad += 1
    else:
        print('  ok  wrapper alone: 6 steps in 12 frames, either parity -- '
              'identical to the gate alone')

    if bad:
        print('\n%d check(s) failed' % bad)
        sys.exit(1)
    print('all good')


if __name__ == '__main__':
    main()
