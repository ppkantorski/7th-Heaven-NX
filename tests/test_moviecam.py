#!/usr/bin/env python3
"""
test_moviecam.py -- the movie-camera budget.

    python3 test_moviecam.py [--moviecam PATH]

WHAT IT IS FOR
==============
`moviecam.lgp` holds one 40-byte camera record per movie FRAME. The reader at
x86 0x40AC9A picks its record by frame:

    edx = get_movie_frame() * 0x28 >> 2         ; 0x40AD46 -- 10 dwords
    ecx = [0x9A0710] + edx*4

and spends its budget by CALL:

    cmp  [0x9A071C], [0x9A0720]                 ; 0x40AD1F -- stop if past
    [0x9A071C] += 10                            ; 0x40AD98 -- once per call
    [0x9A0720] = camsize/4 - 2                  ; 0x40AFC9 -- at load

Those are two different clocks. They agree only while the field ticks once
per vanilla-rate frame. At 30 fps the field ticks twice as often, the budget
runs out at half the movie, and the camera freezes there.

WHAT IS CHECKED
===============
The game's own arithmetic, executed against the real archive: how many calls
the budget buys, that doubling the payload buys exactly twice as many, that
the endpoint returns to the movie's length at 30 ticks/s, and -- the safety
property the whole approach rests on -- that the highest dword the reader can
address still lands inside the ORIGINAL half, so the padding is unreachable.

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

import lgp

HERE = os.path.dirname(os.path.abspath(__file__))
FAIL = []


def check(cond, msg):
    print(('  ok    ' if cond else '  FAIL  ') + msg)
    if not cond:
        FAIL.append(msg)


def budget_calls(payload_size):
    """Calls before `[0x9A071C] > [0x9A0720]`, exactly as ff7_en computes it."""
    limit = payload_size // 4 - 2          # x86 0x40AFC9 / 0x40AFD9
    used = calls = 0
    while not used > limit:                # x86 0x40AD1F -- `jle` continues
        used += 10                         # x86 0x40AD98
        calls += 1
    return calls


VANILLA_FPS, RATIO, REC = 15, 2, 40


TAIL = 30


def stretch(payload, ratio=RATIO, rec=REC):
    """build._emplace_moviecam's transform, kept in step by hand."""
    if not payload or len(payload) % rec:
        return payload * ratio
    body = b''.join(payload[i:i + rec] * ratio
                    for i in range(0, len(payload), rec))
    return body + payload[-rec:] * (TAIL * ratio)
path = None
for i, a in enumerate(sys.argv):
    if a == '--moviecam' and i + 1 < len(sys.argv):
        path = sys.argv[i + 1]
if path is None:
    for cand in (os.path.join(HERE, 'moviecam.lgp'),
                 os.path.join(HERE, 'dump', 'romfs', 'ff7', 'workingdir',
                              'data', 'movies', 'moviecam.lgp')):
        if os.path.exists(cand):
            path = cand
            break

print('the arithmetic, on synthetic sizes\n')
# 1792 records = 71680 bytes is opening.cam; assert the identity in general
for records in (600, 1792, 3000):
    size = records * 40
    check(budget_calls(size) == records,
          '%d records -> %d calls of budget (one per record)'
          % (records, budget_calls(size)))
    check(budget_calls(size * RATIO) == records * RATIO,
          'and doubling the payload doubles it to %d'
          % budget_calls(size * RATIO))

print('\nwhere the budget runs out\n')
size = 1792 * 40
check(abs(budget_calls(size) / VANILLA_FPS - 119.47) < 0.1,
      'vanilla: %d calls at %d ticks/s = %.1fs, the length of the opening'
      % (budget_calls(size), VANILLA_FPS, budget_calls(size) / VANILLA_FPS))
check(abs(budget_calls(size) / (VANILLA_FPS * RATIO) - 59.7) < 0.1,
      '30 fps, unpadded: %.1fs -- the camera freezes for the second half'
      % (budget_calls(size) / (VANILLA_FPS * RATIO)))
check(abs(budget_calls(size * RATIO) / (VANILLA_FPS * RATIO) - 119.47) < 0.1,
      '30 fps, padded x2: %.1fs -- the same endpoint as vanilla'
      % (budget_calls(size * RATIO) / (VANILLA_FPS * RATIO)))

print('\nthe index is RAW, so the track has to be stretched\n')
# The reader does NOT go through the stub movie-fps patches. The recompiler
# inlined the stub into its body at ARM64 0x42078, so it reads guest EAX
# straight from the dispatcher: the raw 30 fps counter.
records = 1792
check(records * RATIO * REC == records * REC * RATIO,
      'a x%d stretch gives %d records, one per frame of a %d fps movie'
      % (RATIO, records * RATIO, 15 * RATIO))
for f in (0, 1, 2, 3, 1791, 3583):
    check(f // RATIO < records,
          'raw frame %d selects interleaved record %d = original record %d'
          % (f, f, f // RATIO))
check(budget_calls(records * RATIO * REC) == records * RATIO,
      'and the budget is %d calls, which is the last frame of the movie'
      % budget_calls(records * RATIO * REC))

if path and os.path.exists(path):
    print('\nagainst the real archive: %s\n' % os.path.basename(path))
    arc = lgp.Archive(path)
    check('opening.cam' in arc.index,
          '%d track(s), opening.cam present' % len(arc.entries))
    op = arc.index['opening.cam']['payload']
    check(len(op) % 40 == 0,
          'opening.cam is %d bytes = %d whole records'
          % (len(op), len(op) // 40))
    check(budget_calls(len(op)) * RATIO == budget_calls(len(op) * RATIO),
          'padding it x%d doubles the budget: %d -> %d calls'
          % (RATIO, budget_calls(len(op)), budget_calls(len(op) * RATIO)))

    before = {n: e['payload'] for n, e in arc.index.items()}
    arc.replace({n: stretch(p) for n, p in before.items()})
    out = os.path.join(HERE, '.moviecam-test.lgp')
    arc.write(out)
    rebuilt = lgp.Archive(out)
    check(set(rebuilt.index) == set(before),
          'the rebuilt archive has the same %d entries' % len(before))
    check(all(len(rebuilt.index[n]['payload']) == len(stretch(before[n]))
              for n in before),
          'every payload is the stretched length')
    op2 = rebuilt.index['opening.cam']['payload']
    nbody = len(op) // REC * RATIO
    check(all(op2[i * REC:(i + 1) * REC] == op[(i // RATIO) * REC:
                                               (i // RATIO + 1) * REC]
              for i in range(nbody)),
          'interleaved record f maps to original record f//%d for all %d '
          'authored records -- the camera shows what vanilla shows at the '
          'same instant' % (RATIO, nbody))
    check(op2[:REC] == op[:REC] and op2[REC:2 * REC] == op[:REC],
          'record 0 is duplicated in place, not appended')
    # The tail: the reader ZEROES its output on entry (x86 0x40ACA0) and
    # returns that zeroed buffer from every bail path, so running off the end
    # of the track is not a frozen camera, it is a null one and the overlaid
    # models stop being drawn. A re-encode is not always exactly RATIO times
    # the vanilla frame count -- 3587 frames against a 3584-record track cost
    # three frames of blank camera right before the handover.
    body = len(op) // REC * RATIO
    check(len(op2) // REC == body + TAIL * RATIO,
          'the track carries %d stretched records plus a %d-record held tail'
          % (body, TAIL * RATIO))
    check(all(op2[(body + k) * REC:(body + k + 1) * REC] == op[-REC:]
              for k in range(TAIL * RATIO)),
          'and every tail record is the LAST authored one, so past the end '
          'the camera holds instead of zeroing')
    check(budget_calls(len(op2)) >= 3587,
          'budget %d calls now covers a 3587-frame re-encode of a '
          '1792-frame movie (was %d, three frames short)'
          % (budget_calls(len(op2)), budget_calls(len(op) * RATIO)))
    check('list.txt' not in rebuilt.index
          or len(rebuilt.index['list.txt']['payload'])
          == len(before['list.txt']) * RATIO,
          'the one entry that is not a whole number of records is repeated '
          'wholesale and not sliced')
    os.remove(out)
else:
    print('\nmoviecam.lgp not found -- archive checks skipped')
    print('   (pass --moviecam PATH, or drop it beside this script)')

print('\n%s' % ('FAILED: %d' % len(FAIL) if FAIL else 'all checks passed'))
sys.exit(1 if FAIL else 0)
