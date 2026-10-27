#!/usr/bin/env python3
"""
Tests for ff7nx_framing.

Three layers, in increasing order of how much they would have saved:

  1. encodings, against the REAL module -- every `expect` read out of `main`
     rather than trusted from a comment;
  2. the cave, WALKED in a patched module -- the branch chain is followed
     instruction by instruction from the hook to the return, so a
     mislaid-out cave cannot pass;
  3. the semantics, by EXECUTING the real setviewport words through
     verify_framing -- which is the layer that caught the unsigned-ucvtf
     problem that three hardware builds did not.

    python3 tests/test_framing.py [path/to/main]

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
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import ff7nx_framing as fr  # noqa: E402

FAIL, N, SKIPS = [], [0], []

# the 60 FPS caves live here; nothing in this module may touch it
TAIL_GAP = (0x1152660, 0x1153000)

MAIN_CANDIDATES = ('dump/exefs/main', 'exefs/main')


def check(c, what):
    N[0] += 1
    if not c:
        FAIL.append(what)


def eq(got, want, what):
    check(got == want, '%s: got %r want %r' % (what, got, want))


def word(s):
    return struct.unpack('<I', bytes(int(b, 16) for b in s.split()))[0]


def _resolve(given=None):
    if given and os.path.exists(given):
        return given
    if given:
        SKIPS.append('%s does not exist, looked elsewhere' % given)
    for rel in MAIN_CANDIDATES:
        for base in ('.', ROOT):
            p = os.path.join(base, rel)
            if os.path.exists(p):
                return p
    return None


# ------------------------------------------------------------ 1. encodings
def test_encodings(main_path):
    p = fr.FIELD_VIEWPORT_PATCH
    old, new = word(p['expect']), word(p['set'])

    # movz Wd, #imm16 -- only the imm16 field may move
    eq(old & ~(0xFFFF << 5) & 0xFFFFFFFF, new & ~(0xFFFF << 5) & 0xFFFFFFFF,
       'viewport patch: only the immediate moves')
    eq((old >> 5) & 0xFFFF, fr.GAME_WIDTH_43, 'viewport patch replaces 640')
    eq((new >> 5) & 0xFFFF, fr.WIDE_VIEWPORT_WIDTH,
       'viewport patch installs 854')

    # the sentinel must not be a width anything else passes
    check(fr.WIDE_VIEWPORT_WIDTH not in fr.STOCK_VIEWPORT_WIDTHS,
          '854 collides with a stock viewport width')

    body = fr.cave_body()
    eq(len(body), 2, 'cave body is two words')
    # cmp w2, #854  -- SUBS wzr, w2, #imm
    eq(body[0] >> 24, 0x71, 'first cave word is a 32-bit cmp immediate')
    eq((body[0] >> 10) & 0xFFF, fr.WIDE_VIEWPORT_WIDTH, 'cmp against 854')
    eq((body[0] >> 5) & 0x1F, 2, 'cmp reads w2 (the width argument)')
    # csel w11, w2, w11, eq
    eq(body[1] & 0xFFE0FC00, 0x1A800000, 'second cave word is csel (32-bit)')
    eq(body[1] & 0x1F, 11, 'csel writes w11 (game_w)')
    eq((body[1] >> 5) & 0x1F, 2, 'csel true-source is w2')
    eq((body[1] >> 16) & 0x1F, 11, 'csel false-source is w11 (unchanged)')
    eq((body[1] >> 12) & 0xF, 0x0, 'csel condition is EQ')

    if not main_path:
        SKIPS.append('encodings vs real main (no module found)')
        return
    import nxmap
    m = nxmap.Main(main_path)
    eq('%08X' % struct.unpack_from('<I', m.text, p['va'])[0], '%08X' % old,
       'viewport patch expect matches the module at +%#x' % p['va'])
    eq('%08X' % struct.unpack_from('<I', m.text, fr.HOOK_VA)[0],
       '%08X' % fr.HOOK_ORIG,
       'hook displaced word matches the module at +%#x' % fr.HOOK_VA)

    # the hook site must be position-independent, or the cave is invalid
    import ff7nx_cave
    try:
        ff7nx_cave.hook(fr.HOOK_VA, fr.HOOK_ORIG, fr.cave_body())
    except ValueError as exc:
        check(False, 'hook site rejected by ff7nx_cave: %s' % exc)

    # and w11 really is loaded from obj[0x954] in the word BEFORE the hook
    prev = struct.unpack_from('<I', m.text, fr.HOOK_VA - 4)[0]
    eq('%08X' % prev, '%08X' % 0xB949552B,
       'the word before the hook is ldr w11, [x9, #0x954]')


# ------------------------------------------------------ 2. walk the cave
def test_cave_is_walkable(main_path):
    """
    Follow the branch chain in a patched module, instruction by instruction,
    and assert the executed sequence is exactly cmp, csel, displaced, return.
    A cave split across holes (which this one is) still has to read straight.
    """
    if not main_path:
        SKIPS.append('cave walk (no module found)')
        return
    import tempfile
    import nxmap
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, 'main')
        lines = []
        if not fr.apply_to_nso(main_path, out, log=lines.append):
            check(False, 'apply_to_nso failed: %s' % '\n'.join(lines))
            return
        before = nxmap.Main(main_path)
        after = nxmap.Main(out)

        def w(img, va):
            return struct.unpack_from('<I', img, va)[0]

        seq, va, steps = [], fr.HOOK_VA, 0
        while steps < 32:
            steps += 1
            insn = w(after.text, va)
            if (insn & 0xFC000000) == 0x14000000:      # unconditional b
                imm = insn & 0x03FFFFFF
                if imm & 0x02000000:
                    imm -= 0x04000000
                va = va + imm * 4
                if va == fr.HOOK_VA + 4:
                    break
                continue
            seq.append(insn)
            va += 4
        check(steps < 32, 'cave chain terminates')
        eq(va, fr.HOOK_VA + 4, 'cave returns to the word after the hook')
        eq(['%08X' % x for x in seq],
           ['%08X' % x for x in fr.cave_body() + [fr.HOOK_ORIG]],
           'executed sequence is cmp, csel, displaced')

        # every cave word must have landed on padding that was zero
        diff = [i for i in range(0, len(before.text), 4)
                if before.text[i:i + 4] != after.text[i:i + 4]]
        expected_edits = {fr.FIELD_VIEWPORT_PATCH['va'], fr.HOOK_VA}
        for va2 in diff:
            if va2 in expected_edits:
                continue
            eq(w(before.text, va2), 0,
               'cave word at +%#x overwrote padding that was zero' % va2)

        eq(len(diff), len(set(diff)), 'no duplicate edits')
        check(fr.FIELD_VIEWPORT_PATCH['va'] in diff, 'viewport word patched')
        check(fr.HOOK_VA in diff, 'hook word patched')

        # the 60 FPS tail gap must be untouched
        intruders = [v for v in diff if TAIL_GAP[0] <= v < TAIL_GAP[1]]
        eq(intruders, [], 'nothing written into the 60 FPS tail gap')

        # and the change is small -- if this ever balloons, something is wrong
        check(len(diff) <= 10,
              '.text diff is %d words (expected <= 10)' % len(diff))
        print('  cave laid out across %d word(s); .text diff %d words'
              % (len(diff) - 2, len(diff)))


# --------------------------------------------------- 3. execute the words
def test_semantics(main_path):
    """
    Delegate to verify_framing, which executes setviewport's real words with
    the cave in place and checks that ONLY the widened rect moves.
    """
    if not main_path:
        SKIPS.append('semantics (no module found)')
        return
    if not os.path.exists(os.path.join(ROOT, 'dump', 'exefs', 'main')):
        SKIPS.append('semantics (verify_framing needs exefs/main beside it)')
        return
    try:
        import verify_framing
    except ImportError as exc:
        SKIPS.append('semantics (%s)' % exc)
        return
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = verify_framing.main()
    eq(rc, 0, 'verify_framing: %s' % buf.getvalue()[-400:])
    check(verify_framing.N[0] >= 15,
          'verify_framing ran %d checks' % verify_framing.N[0])
    print('  verify_framing: %d checks executing the real words'
          % verify_framing.N[0])


def main():
    main_path = _resolve(sys.argv[1] if len(sys.argv) > 1 else None)
    print('main    : %s\n' % (main_path or '-- not found --'))
    test_encodings(main_path)
    test_cave_is_walkable(main_path)
    test_semantics(main_path)
    print()
    for s in SKIPS:
        print('SKIP  %s' % s)
    if FAIL:
        for f in FAIL:
            print('FAIL  %s' % f)
        print('\n%d/%d checks failed' % (len(FAIL), N[0]))
        return 1
    print('%d checks passed%s' % (N[0],
          ', %d group(s) skipped' % len(SKIPS) if SKIPS else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
