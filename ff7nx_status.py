#!/usr/bin/env python3
r"""
ff7nx_status.py -- what is ACTUALLY in this module, and do the caves collide.

    python3 ff7nx_status.py sdout/atmosphere/contents/0100A5B00BDC6000/exefs/main

Written because "it's like some of them get turned off unintentionally" is a
real observation with a boring cause, and the only way to stop guessing is a
single command that reads the shipped file.

WHY THINGS LOOK LIKE THEY TURN OFF
==================================
Not cave collisions -- this script proves those are absent. Three duller
reasons, all of which have actually happened:

1. `build.py` REGENERATES main from `dump/exefs/main` on every build and
   re-applies its own chain. So anything you `--apply` by hand AFTER a build
   is gone at the next build, and anything you `--revert` by hand comes back.
   (This is how the model cull went 97/457 -> 40/400 -> 97/457.)

2. `build.py` applies whatever version of a module file is in the repo. Copy
   a new ff7nx_*.py into my folder but not into the tree and the build ships
   the OLD behaviour, silently, while your hand-run of the new file reports
   success against a file the build then overwrites.

3. A module can report "already installed" for the leg it checks while a
   SECOND leg of the same module is missing -- ff7nx_movieclip's state() only
   looked at the cave hook, so the full-screen bypass could be absent and the
   module still said INSTALLED. Fixed there; this script checks every leg of
   every module so it cannot recur elsewhere.

The rule that falls out: **build, then patch, then copy to the SD card -- and
run this script last.** Never patch before a build.
"""
from __future__ import annotations

import collections
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


# ---------------------------------------------------------------- cave walk
def _walk(W, hook):
    """Every word of the chained cave hooked at `hook`, or None if unhooked."""
    w = W(hook)
    if (w & 0xFC000000) != 0x14000000:
        return None
    imm = w & 0x03FFFFFF
    if imm & (1 << 25):
        imm -= 1 << 26
    va, seen = hook + imm * 4, [hook]
    for _ in range(80):
        x = W(va)
        seen.append(va)
        if (x & 0xFC000000) == 0x14000000:
            j = x & 0x03FFFFFF
            if j & (1 << 25):
                j -= 1 << 26
            tgt = va + j * 4
            if tgt == hook + 4:
                return seen
            va = tgt
            continue
        va += 4
    raise RuntimeError('cave chain from +%#x did not terminate' % hook)


# hook sites that carry a cave: (module, leg, va)
CAVES = [
    ('letterbox',  'viewport y (leg 3)',        0x9298EC),
    ('letterbox',  '[0xCFF208] (v4 defect)',    0x9299D0),
    ('letterbox',  'uncrop setviewport',        0x10D67C8),
    ('letterbox',  'uncrop gl_load_state',      0x10D9458),
    ('letterbox',  'uncrop begin_scene',        0x10D9E34),
    ('letterbox',  'fade quad x',               0x9F3A24),
    ('letterbox',  'fade quad y',               0x9F3A44),
    ('letterbox',  'fade quad w',               0x9F3A64),
    ('letterbox',  'fade quad h',               0x9F3A84),
    ('framing',    'tile cull bottom1',         0xA071C8),
    ('framing',    'tile cull bottom2',         0xA05D84),
    ('framing',    'tile cull right1',          0xA072D0),
    ('framing',    'tile cull right2',          0xA05E8C),
    ('framing',    'parallax right (l4b)',      0xA08D40),
    ('moviealign', 'movie quad +16',            0x10DE8F0),
    ('camclamp',   'scripted camera clamp',     0x9F874C),
    # RETIRED -- see ff7nx_movieclip.enabled(). Hooked here means the scissor
    # is narrowing the whole frame during an FMV, which freezes stale field
    # art in the 16:9 margins. `not hooked` is now the wanted state.
    ('movieclip',  'scissor band  (RETIRED)',   0x1133FE8),
]

# caves whose ABSENCE is correct: {hook: why}
CAVES_WANT_ABSENT = {
    0x1133FE8: 'RETIRED -- the scissor ate the margin clear; '
               'ff7nx_moviecull replaces it',
}

# single-word legs: (module, leg, va, {word: meaning}, wanted)
SINGLES = [
    ('letterbox',  'painted bars',       0x10F3DDC,
     {0xBD00CD40: 'ON  (stock)', 0xB900CD5F: 'off'}, 'off'),
    ('letterbox',  'field frame h',      0x9298BC,
     {0x321A0BE8: '448 (stock)', 0x52803C08: '480  <- stretches models'}, '448 (stock)'),
    ('letterbox',  'layer 1 origin',     0xA06EA8,
     {0x321B0BE9: '224 (stock)', 0x52801D09: '232'}, '232'),
    ('letterbox',  'layer 2 origin',     0xA05AA4,
     {0x321B0BE9: '224 (stock)', 0x52801D09: '232'}, '232'),
    ('letterbox',  'layer 3 origin',     0xA07878,
     {0x321B0BE9: '224 (stock)', 0x52801D09: '232'}, '232'),
    ('letterbox',  'layer 4 origin',     0xA08728,
     {0x321B0BE9: '224 (stock)', 0x52801D09: '232'}, '232'),
    ('letterbox',  'sprite origin',      0x929964,
     {0x321B0BE8: '224 (stock)', 0x52801E08: '240'}, '240'),
    ('modelcull',  'cull left',          0x9EC43C,
     {0x5100A109: '40 (stock 4:3)', 0x51018509: '97  (16:9)'}, '97  (16:9)'),
    ('modelcull',  'cull right',         0x9EC49C,
     {0x11064108: '400 (stock 4:3)', 0x11072508: '457 (16:9)'}, '457 (16:9)'),
    # RETIRED with the module. The stock b.eq being PRESENT is now correct:
    # it means the scissor path early-outs on a full-frame box, which is what
    # we want now that nothing narrows it.
    ('movieclip',  'bypass (RETIRED)',   0x11377C4,
     {0x54000160: 'stock  (correct -- movieclip is retired)',
      0xD503201F: 'removed  <-- movieclip is still installed'},
     'stock  (correct -- movieclip is retired)'),
]

# The two modelcull sites are TAKEN OVER by ff7nx_moviecull's caves when the
# movie gate is installed, so a raw word lookup reports UNRECOGNISED for a
# perfectly healthy module. Ask the module instead of guessing.
MOVIECULL_SITES = {0x9EC43C: 'cull left', 0x9EC49C: 'cull right'}


def _moviecull_row(t, va):
    """(text, wanted) for a modelcull site that ff7nx_moviecull has caved."""
    try:
        import ff7nx_moviecull as MC
    except Exception:
        return None
    if MC.cave_state(t, va) != 'patched':
        return None
    pl, wl, pr, wr = MC.bounds_in_module(t)
    play, wide = (pl, wl) if va == MC.LEFT_SITE else (pr, wr)
    txt = 'CAVE: %s while a movie plays, else %s' % (play, wide)
    want_play = MC.STOCK_LEFT if va == MC.LEFT_SITE else MC.STOCK_RIGHT
    want_wide = MC.WIDE_LEFT if va == MC.LEFT_SITE else MC.WIDE_RIGHT
    return txt, 'CAVE: %s while a movie plays, else %s' % (want_play, want_wide)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__.split('\n')[3].strip())
        return 2
    path = argv[0]
    if os.path.isdir(path):
        path = os.path.join(path, 'atmosphere', 'contents',
                            '0100A5B00BDC6000', 'exefs', 'main')
    import nso_tool
    t = nso_tool.parse_nso(path)['segments']['.text']['data']
    W = lambda va: struct.unpack_from('<I', t, va)[0]          # noqa: E731

    print(path)
    print()
    print('  single-word legs')
    warn = []
    for mod, leg, va, table, want in SINGLES:
        got = W(va)
        if va in MOVIECULL_SITES:
            row = _moviecull_row(t, va)
            if row is not None:
                what, want = row
                mod = 'moviecull'
            else:
                what = table.get(got,
                                 'UNRECOGNISED %08X -- do not patch' % got)
        else:
            what = table.get(got, 'UNRECOGNISED %08X -- do not patch' % got)
        if want is None:
            print('    +%#09x  %-11s %-20s %s   (informational)'
                  % (va, mod, leg, what))
            continue
        flag = '' if what == want else '   <-- not the wanted state'
        if what != want:
            warn.append('%s / %s is %s' % (mod, leg, what))
        print('    +%#09x  %-11s %-20s %s%s' % (va, mod, leg, what, flag))

    print()
    print('  caves')
    own = collections.defaultdict(list)
    live = 0
    for mod, leg, hook in CAVES:
        ws = _walk(W, hook)
        if ws is None:
            note = 'not hooked'
            if leg.startswith('[0xCFF208]'):
                note = 'not hooked  (correct -- this leg must stay stock)'
            elif hook in CAVES_WANT_ABSENT:
                note = 'not hooked  (correct -- %s)' % CAVES_WANT_ABSENT[hook]
            else:
                warn.append('%s / %s is not hooked' % (mod, leg))
            print('    +%#09x  %-11s %-24s %s' % (hook, mod, leg, note))
            continue
        live += 1
        extra = ''
        if hook in CAVES_WANT_ABSENT:
            extra = '   <-- should NOT be hooked (%s)' % CAVES_WANT_ABSENT[hook]
            warn.append('%s / %s is hooked and should not be' % (mod, leg))
        print('    +%#09x  %-11s %-24s %2d word(s)%s'
              % (hook, mod, leg, len(ws), extra))
        for v in ws:
            own[v].append('%s/%s' % (mod, leg))

    print()
    dup = {v: n for v, n in own.items() if len(n) > 1}
    if dup:
        print('  *** CAVE COLLISION -- two legs share a word ***')
        for v, n in sorted(dup.items()):
            print('    +%#09x  %s' % (v, n))
        warn.append('%d cave word(s) claimed twice' % len(dup))
    else:
        print('  caves: %d live, %d distinct words, no overlap'
              % (live, len(own)))

    inside = [(m, l, v) for m, l, v, _t, _w in SINGLES if v in own]
    if inside:
        print('  *** a single-word patch sits inside a cave: %s ***' % inside)
        warn.append('single-word patch inside a cave')
    else:
        print('  no single-word patch lands inside a cave')

    print()
    if warn:
        print('  %d thing(s) to look at:' % len(warn))
        for w in warn:
            print('    ! ' + w)
        return 1
    print('  everything is in the wanted state')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
