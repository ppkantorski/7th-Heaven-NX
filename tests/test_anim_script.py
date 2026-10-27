#!/usr/bin/env python3
"""
test_anim_script.py -- the battle.lgp `?ab` opcode table and walker.

    python3 test_anim_script.py [--lgp path/to/battle.lgp]

WHY THIS EXISTS
===============
The previous opcode table had 36 of the 110 entries and treated an unknown
opcode as a CLEAN END. Every script therefore "parsed successfully" after a
median of one byte, the guard that only modifies cleanly-parsed scripts never
fired, and 76 of 725 wait operands were scaled. The `?ab` scripts were in
practice unscaled while the animations they wait on were four times longer.

Nothing crashed. Nothing logged a warning. The only visible symptom was battle
beats landing on top of each other. So this file checks three things that would
each have caught it:

  1. TRANSCRIPTION. ANIM_OPS is compared entry by entry against FFNx's
     `numArgsOpCode` from src/ff7/battle/animations.h, reproduced below as an
     independent literal. Every deliberate difference has to be declared here,
     so adding one silently is a failure.
  2. BEHAVIOUR. Hand-built scripts exercise each opcode class -- operands,
     switch-handled cases, the 0xFE/0xC0 restart, terminators, and an unknown
     opcode -- and the walker must return exactly the right wait offsets and the
     right ok flag. An unknown opcode must REFUSE, which is the specific
     inversion that hid the bug.
  3. COVERAGE, against a real archive if one is given. Parsing under 95% aborts
     a build now; this asserts the real number is far above that, that scaling
     is idempotent in structure, and that a scaled archive is detected as
     already scaled.

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
import argparse
import struct
import sys

import ff7nx_60fps as F

FAIL = []


def fail(msg):
    FAIL.append(msg)
    print('  FAIL  %s' % msg)


# --------------------------------------------------------------------------
# FFNx src/ff7/battle/animations.h, numArgsOpCode -- transcribed independently
# of ANIM_OPS so the two can disagree. 0xFF here is FFNx's marker for "handled
# in the switch"; -1 is 0xF1.
FFNX = {
    0x8E: 0, 0x8F: 0, 0x90: 3, 0x91: 1, 0x92: 0, 0x93: 0, 0x94: 5, 0x95: 0,
    0x96: 2, 0x97: 2, 0x98: 1, 0x99: 6, 0x9A: 4, 0x9B: 0, 0x9C: 0, 0x9D: 1,
    0x9E: 0xFF, 0x9F: 0, 0xA0: 1, 0xA1: 2, 0xA2: 1, 0xA3: 1, 0xA4: 0, 0xA5: 0,
    0xA6: 0, 0xA7: 1, 0xA8: 2, 0xA9: 2, 0xAA: 0, 0xAB: 4, 0xAC: 1, 0xAD: 5,
    0xAE: 0, 0xAF: 1, 0xB0: 0, 0xB1: 0, 0xB2: 0, 0xB3: 0xFF, 0xB4: 0, 0xB5: 11,
    0xB6: 1, 0xB7: 0, 0xB8: 0, 0xB9: 1, 0xBA: 2, 0xBC: 1, 0xBD: 4, 0xBE: 1,
    0xBF: 2, 0xC1: 0xFF, 0xC2: 1, 0xC3: 0, 0xC4: 3, 0xC5: 0, 0xC6: 1,
    0xC7: 3, 0xC8: 5, 0xC9: 0, 0xCA: 0xFF, 0xCB: 8, 0xCC: 1, 0xCE: 0xFF,
    0xCF: 8, 0xD0: 3, 0xD1: 5, 0xD2: 0, 0xD3: 0, 0xD4: 3, 0xD5: 8, 0xD6: 1,
    0xD7: 2, 0xD8: 3, 0xDA: 1, 0xDB: 4, 0xDC: 3, 0xDD: 2, 0xDE: 2, 0xDF: 0,
    0xE0: 0, 0xE1: 0, 0xE2: 0, 0xE3: 0, 0xE4: 0, 0xE5: 0, 0xE6: 0, 0xE7: 1,
    0xE8: 0, 0xE9: 3, 0xEA: 0, 0xEB: 0xFF, 0xEC: 0xFF, 0xED: 0, 0xEE: 0xFF,
    0xF0: 0, 0xF1: -1, 0xF2: 0, 0xF3: 0xFF, 0xF4: 0xFF, 0xF5: 1, 0xF6: 0,
    0xF7: 1, 0xF8: 1, 0xF9: 0, 0xFA: 0, 0xFB: 4, 0xFC: 0, 0xFD: 6,
    0xFE: 0xFF, 0xFF: 0xFF,
}

# Every place this walker deliberately differs from a literal reading of that
# table, with the reason. Anything not listed here must match exactly.
DECLARED = {
    0x9E: 'S',   # switch: ends the frame, consumes no operand
    0xB3: 'S',   # switch: conditional skip to 0xB2
    0xC1: 'S',   # switch: restart, scans to 0xC9
    0xCA: 'S',   # switch: conditional restart
    0xCE: 'S',   # switch: conditional skip to 0xCD (table entry is dead code)
    0xEB: 'S',   # switch: conditional frame end
    0xEC: 'S',
    0xF3: 'S',   # switch: wait tick
    0xC6: 'W',   # switch: 1 operand, the GLOBAL wait -- scaled
    0xF4: 'W',   # switch: 1 operand, the ACTOR wait -- scaled
    0xEE: 'T',   # switch: script ends
    0xFF: 'T',   # switch: script ends
    0xFE: 'P',   # switch: PEEKS the next byte; 0xC0 there ends the script
    0xF1: 'T',   # endingOpCode with -1 args: position moves back, so it repeats
                 # forever. Terminal here -- faithful, and it raises clean
                 # parses from 99.4% to 99.7%.
}
# Present in ANIM_OPS but absent from FFNx's table, with the reason.
ADDED = {
    0xCD: 0,     # only ever the target 0xCE scans to; a nop if reached directly
}


def check_table():
    ours = F.ANIM_OPS
    for op, want in sorted(FFNX.items()):
        got = ours.get(op)
        if op in DECLARED:
            if got != DECLARED[op]:
                fail('opcode %02X: declared as %r, table has %r'
                     % (op, DECLARED[op], got))
            continue
        if got is None:
            fail('opcode %02X missing from ANIM_OPS (FFNx says %d args)'
                 % (op, want))
        elif got != want:
            fail('opcode %02X: FFNx says %d operand byte(s), table says %r'
                 % (op, want, got))
    for op in sorted(set(ours) - set(FFNX)):
        if op not in ADDED:
            fail('opcode %02X is in ANIM_OPS but not in FFNx\'s table and is '
                 'not declared as an addition' % op)
        elif ours[op] != ADDED[op]:
            fail('added opcode %02X: declared %r, table has %r'
                 % (op, ADDED[op], ours[op]))
    n_switch = sum(1 for v in ours.values() if v in ('S', 'W', 'T', 'P'))
    print('  ok    %d opcode(s), %d switch-handled, matches FFNx '
          'numArgsOpCode' % (len(ours), n_switch))


def walk(script):
    b = bytes(script)
    waits, ok = F.walk_anim_script(b, 0, len(b))
    return waits, ok


def check_behaviour():
    cases = [
        ('plain terminator', [0x8E, 0xFF], [], True),
        ('animation index then end', [0x05, 0x20, 0xFF], [], True),
        ('actor wait 0xF4', [0xF4, 0x0F, 0xFF], [1], True),
        ('global wait 0xC6 + apply 0xC5',
         [0xC6, 0x19, 0xC5, 0xFF], [1], True),
        ('two waits', [0xF4, 0x02, 0x90, 1, 2, 3, 0xC6, 0x40, 0xFF],
         [1, 7], True),
        ('operands are not mistaken for opcodes',
         [0xB5] + [0xF4] * 11 + [0xFF], [], True),
        ('0xFE 0xC0 restart is a terminator', [0xF4, 0x08, 0xFE, 0xC0], [1],
         True),
        ('0xFE not followed by 0xC0 continues', [0xFE, 0x8E, 0xFF], [], True),
        ('0xF1 is terminal', [0xF1, 0xF4, 0x99], [], True),
        ('unknown opcode REFUSES', [0xF4, 0x04, 0xBB, 0xFF], [], False),
        ('running off the end refuses', [0x8E, 0x90, 1, 2], [], False),
        ('a wait with no operand refuses', [0x8E, 0xF4], [], False),
        ('switch-handled ops consume nothing',
         [0x9E, 0xB3, 0xC1, 0xCA, 0xCE, 0xEB, 0xEC, 0xF3, 0xFF], [], True),
    ]
    for name, script, want_waits, want_ok in cases:
        waits, ok = walk(script)
        if ok != want_ok:
            fail('%s: ok=%s, wanted %s' % (name, ok, want_ok))
        elif want_ok and waits != want_waits:
            fail('%s: wait offsets %s, wanted %s' % (name, waits, want_waits))
        else:
            print('  ok    %-42s %s' % (name, 'refused' if not ok
                                        else 'waits at %s' % waits))


def check_archive(path, mult):
    raw = open(path, 'rb').read()
    print('\n%s  %d bytes' % (path, len(raw)))

    def coverage(d):
        tot = clean = nwait = 0
        for name, off, size in F.lgp_entries(d):
            if len(name) != 4 or not name.lower().endswith('ab') or size < 0x6C:
                continue
            first, = struct.unpack('<I', d[off + 0x68:off + 0x6C])
            cnt = (first - 0x68) // 4
            if cnt <= 0 or cnt > 512 or 0x68 + 4 * cnt > size:
                continue
            seen = set()
            for k in range(cnt):
                o, = struct.unpack('<I', d[off + 0x68 + 4 * k:
                                           off + 0x6C + 4 * k])
                if not (0 < o < size) or o in seen:
                    continue
                seen.add(o)
                if d[off + o] == 0:
                    continue
                tot += 1
                w, ok = F.walk_anim_script(d, off + o, off + size)
                if ok:
                    clean += 1
                    nwait += len(w)
        return tot, clean, nwait

    tot, clean, nwait = coverage(raw)
    pct = 100.0 * clean / tot if tot else 0
    print('  stock : %d scripts, %d clean (%.1f%%), %d wait operand(s)'
          % (tot, clean, pct, nwait))
    if pct < 99.0:
        fail('only %.1f%% of scripts parse end to end' % pct)
    else:
        print('  ok    coverage %.1f%% (the build gate is 95%%)' % pct)
    if nwait < 500:
        fail('only %d wait operands found -- the old broken table found 76'
             % nwait)

    out = F.patch_battle_lgp(raw, mult, log=lambda *a: None)
    if len(out) != len(raw):
        fail('scaling changed the archive size')
    tot2, clean2, nwait2 = coverage(out)
    if (tot2, clean2, nwait2) != (tot, clean, nwait):
        fail('the scaled archive parses differently: %s vs %s'
             % ((tot2, clean2, nwait2), (tot, clean, nwait)))
    else:
        print('  ok    scaled archive parses identically -- %d scripts, %d '
              'clean, %d waits' % (tot2, clean2, nwait2))

    # every changed byte must be exactly min(stock * mult, 255) at a wait offset
    diffs = [i for i in range(len(raw)) if raw[i] != out[i]]
    offs = set()
    for name, off, size in F.lgp_entries(raw):
        if len(name) != 4 or not name.lower().endswith('ab') or size < 0x6C:
            continue
        first, = struct.unpack('<I', raw[off + 0x68:off + 0x6C])
        cnt = (first - 0x68) // 4
        if cnt <= 0 or cnt > 512 or 0x68 + 4 * cnt > size:
            continue
        seen = set()
        for k in range(cnt):
            o, = struct.unpack('<I', raw[off + 0x68 + 4 * k:
                                        off + 0x6C + 4 * k])
            if not (0 < o < size) or o in seen:
                continue
            seen.add(o)
            if raw[off + o] == 0:
                continue
            w, ok = F.walk_anim_script(raw, off + o, off + size)
            if ok:
                offs |= set(w)
    stray = [i for i in diffs if i not in offs]
    if stray:
        fail('%d byte(s) changed outside a wait operand, first at 0x%X'
             % (len(stray), stray[0]))
    else:
        print('  ok    %d changed byte(s), all of them wait operands'
              % len(diffs))
    bad = [i for i in diffs if out[i] != min(raw[i] * mult, 255)]
    if bad:
        fail('%d operand(s) not scaled as min(v*%d, 255)' % (len(bad), mult))
    else:
        clamped = sum(1 for i in offs if raw[i] * mult > 255)
        print('  ok    every operand is min(v*%d, 255); %d hit the clamp, '
              'exactly as FFNx clamps them' % (mult, clamped))

    if not F.battle_lgp_looks_patched(out, mult)[0]:
        fail('a scaled archive is not detected as already scaled -- it could '
             'be scaled twice')
    elif F.battle_lgp_looks_patched(raw, mult)[0]:
        fail('a stock archive is misdetected as already scaled')
    else:
        print('  ok    double-scale guard: stock=no, scaled=yes')

    # The dangerous middle state: an archive scaled by the PREVIOUS broken
    # walker. It reads as "not patched" to the divisibility guard, so without a
    # dedicated detector the 76 waits it did reach would be scaled a second time
    # to x16 while the other 649 finally reached x4.
    old = bytearray(raw)
    for w in F._wait_sets(raw, F._legacy_walk_anim_script):
        old[w] = min(old[w] * mult, 255)
    old = bytes(old)
    states = [('stock', raw, False), ('scaled by the old walker', old, True),
              ('scaled by this walker', out, False)]
    bad = False
    for tag, blob, want in states:
        got, n_old, f_old, n_new, f_new = \
            F.battle_lgp_scaled_by_old_walker(blob, mult)
        if got != want:
            fail('old-walker detector says %s for "%s" (reachable %d at %.0f%%,'
                 ' rest %d at %.0f%%)' % (got, tag, n_old, f_old * 100, n_new,
                                          f_new * 100))
            bad = True
    if not bad:
        print('  ok    old-walker detector: stock=no, half-scaled=YES, '
              'fully-scaled=no')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lgp', help='a CLEAN battle.lgp to measure against')
    ap.add_argument('--mult', type=int, default=4)
    a = ap.parse_args()
    print('?ab opcode table\n')
    check_table()
    print('\nwalker behaviour:')
    check_behaviour()
    if a.lgp:
        check_archive(a.lgp, a.mult)
    else:
        print('\n(no --lgp given; skipping the archive coverage check)')
    print('\n%d failure(s)' % len(FAIL))
    if FAIL:
        return 1
    print('PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
