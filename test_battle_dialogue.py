#!/usr/bin/env python3
"""
test_battle_dialogue.py -- the inlined copy of the battle text duration.

    python3 test_battle_dialogue.py [--nso path/to/exefs/main]

`get_n_frames_display_action_string` (x86 0x5BE475) is four instructions, and
the static recompiler emitted it TWICE: once out of line, and once inlined into
its only caller `add_text_to_display_queue` (x86 0x5BDFB6). `battle-text`
originally patched only the out-of-line copy, so anything reaching the default
through the inline one kept a quarter of its duration -- which is the scripted
battle dialogue, the `n_frames == 0` case.

The guard that matters here is the LAST check: exactly two copies of the
pattern exist in .text and the group covers both. If a future module inlines it
a third time, that fails rather than shipping half fixed.
"""
import argparse
import struct
import sys

import capstone

import ff7nx_60fps as F

MD = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
ASR2 = 0x13027D08          # asr w8, w8, #2
ADD4 = 0x11001108          # add w8, w8, #4
ASR0 = 0x13007D08          # asr w8, w8, #0   (i.e. keep all four quarters)
ADD16 = 0x11004108         # add w8, w8, #16
FAIL = []


def ok(cond, what):
    print(('  ok  ' if cond else '  FAIL  ') + what)
    if not cond:
        FAIL.append(what)


def dis(w):
    i = next(MD.disasm(struct.pack('<I', w), 0))
    return (i.mnemonic + ' ' + i.op_str).strip()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--nso', default='dump/exefs/main')
    a = ap.parse_args(argv)

    print('the transformation is x4, and both halves of it')
    ok(dis(ASR2) == 'asr w8, w8, #2' and dis(ASR0) == 'asr w8, w8, #0',
       'the shift goes from >>2 to >>0')
    ok(dis(ADD4) == 'add w8, w8, #4' and dis(ADD16) == 'add w8, w8, #0x10',
       'and the addend from 4 to 16 -- 4 * ((b>>2) + 4) == (b & ~3) + 16')

    group = F.NSO_GATED['battle-text']
    ok(len(group) == 4, 'battle-text carries four word patches, not two')
    sites = {va: (old, new) for _l, va, old, new in group}
    for va in (0x7CF280, 0x7CF188):
        ok(sites.get(va) == (ASR2, ASR0), 'the shift at 0x%X is patched' % va)
    for va in (0x7CF284, 0x7CF18C):
        ok(sites.get(va) == (ADD4, ADD16), 'the addend at 0x%X is patched' % va)

    try:
        import nxmap
        m = nxmap.Main(a.nso)
    except Exception as exc:
        print('\nmodule tests SKIPPED -- pass --nso /path/to/exefs/main (%s)'
              % exc)
        return 1 if FAIL else 0
    img = m.img

    print()
    print('against the stock module')
    for va, want in sites.items():
        got = struct.unpack_from('<I', img, va)[0]
        ok(got == want[0], '0x%X is `%s`' % (va, dis(want[0])))

    import bisect
    starts = sorted(m.arm_starts)
    arm2x86 = {v: k for k, v in m.x86_to_arm.items()}

    def owner(x):
        return arm2x86[starts[bisect.bisect_right(starts, x) - 1]]

    ok(owner(0x7CF280) == 0x5BE475,
       'the out-of-line copy is in get_n_frames_display_action_string')
    ok(owner(0x7CF188) == 0x5BDFB6,
       'and the inline one is in add_text_to_display_queue -- a DIFFERENT '
       'function, which is why patching the first missed it')

    # THE guard: no third copy
    found = []
    for off in range(0, 0x1152660 - 4, 4):
        if struct.unpack_from('<I', img, off)[0] == ASR2 \
                and struct.unpack_from('<I', img, off + 4)[0] == ADD4:
            found.append(off)
    ok(found == [0x7CF188, 0x7CF280],
       'there are exactly TWO copies of the pattern in .text and the group '
       'covers both: %s' % ['0x%X' % f for f in found])

    print()
    if FAIL:
        print('%d FAILED' % len(FAIL))
        return 1
    print('all good')
    return 0


if __name__ == '__main__':
    sys.exit(main())
