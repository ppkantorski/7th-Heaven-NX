#!/usr/bin/env python3
"""
test_texcache.py -- disabling the port's (w,h)-keyed texture cache.

Unit checks run anywhere. The ones that matter need `exefs/main` and are
SKIPPED rather than failed when no dump is present.

    python3 test_texcache.py
    python3 test_texcache.py --main <some other exefs/main>

WHAT EACH GROUP IS FOR
----------------------
1. table     -- the two words the patch chooses between, and the fact that
                both branch to the SAME destination. If they ever did not,
                the "patched" build would be jumping somewhere arbitrary.
2. mode      -- the environment override, and that the DEFAULT IS OFF. That
                last one is the FINDINGS-304 lesson expressed as a test: a
                plain build must not quietly ship this.
3. signature -- verify() against the real module and against mutations of
                it. Every anchor is load-bearing, and the gate is accepted
                in BOTH resting states so the pass is re-runnable over its
                own output.
4. write     -- exactly one word changes, it is the right one, every mode
                round-trips, and stock comes back byte-identical.
5. destroy   -- the branch we force is REACHABLE IN STOCK and complete: it
                releases both surfaces, nulls both pointers, frees the
                container and clears the slot. This is the group that says
                the patch is safe rather than merely small.
6. wiring    -- the build pass exists, the GUI calls it, it runs after
                apply_glerror, and the env names agree.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import struct
import sys
import tempfile

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import ff7nx_texcache as T                                     # noqa: E402

FAILURES: list[str] = []
SKIPPED: list[str] = []


def check(label, ok, detail=''):
    print('  %-58s %s%s' % (label, 'ok' if ok else 'FAIL',
                            ('  -- ' + detail) if detail and not ok else ''))
    if not ok:
        FAILURES.append(label + (('  ' + detail) if detail else ''))
    return ok


def skip(label, why):
    print('  %-58s skipped  -- %s' % (label, why))
    SKIPPED.append(label)


def find_main(explicit=None):
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None
    for rel in ('game_data_files/exefs/main', 'dump/exefs/main',
                'sdout/atmosphere/contents/0100A5B00BDC6000/exefs/main'):
        p = HERE / rel
        if p.is_file():
            return p
    return None


def _target(word, va):
    """Where an A64 B / B.cond at `va` lands."""
    if (word >> 26) == 0x05:                       # unconditional B
        imm = word & 0x3FFFFFF
        if imm & 0x2000000:
            imm -= 0x4000000
        return va + imm * 4
    imm = (word >> 5) & 0x7FFFF                    # B.cond
    if imm & 0x40000:
        imm -= 0x80000
    return va + imm * 4


def test_table():
    print('\ntable')
    check('selftest', T.selftest(lambda *_: None))
    check('two modes', set(T.MODES) == {'nocache', 'off'})
    check('the stock word is a b.hi', (T.GATE_STOCK >> 24) == 0x54
          and (T.GATE_STOCK & 0xF) == 8)
    check('the patched word is an unconditional b',
          (T.GATE_NOCACHE >> 26) == 0x05)
    # The whole safety argument rests on this: we are not redirecting the
    # branch, only making it always taken.
    check('both words branch to the SAME address',
          _target(T.GATE_STOCK, T.GATE_VA)
          == _target(T.GATE_NOCACHE, T.GATE_VA) == T.DESTROY_VA,
          '%X / %X' % (_target(T.GATE_STOCK, T.GATE_VA),
                       _target(T.GATE_NOCACHE, T.GATE_VA)))
    check('the cap really is ten per key (cmp x0, #9)',
          T.CMP_WORD == 0xF100241F)
    check('anchor addresses are unique',
          len({a for a, _, _ in T.ANCHORS}) == len(T.ANCHORS))
    check('word_for round-trips',
          T.word_for('nocache') == T.GATE_NOCACHE
          and T.word_for('off') == T.GATE_STOCK)


def test_mode():
    print('\nmode from the environment')
    E = T.MODE_ENV
    check('unset -> the code constant', T.mode({}) == T.MODE)
    check('empty -> the code constant', T.mode({E: ''}) == T.MODE)
    check('"nocache" -> nocache', T.mode({E: 'nocache'}) == 'nocache')
    check('"1" -> nocache', T.mode({E: '1'}) == 'nocache')
    check('"ON" -> nocache (case folded)', T.mode({E: 'ON'}) == 'nocache')
    check('"off" -> off', T.mode({E: 'off'}) == 'off')
    check('"0" -> off', T.mode({E: '0'}) == 'off')
    check('garbage -> the code constant', T.mode({E: 'banana'}) == T.MODE)
    # FINDINGS-304 §6, as a test rather than a paragraph.
    check('THE DEFAULT IS OFF (a plain build writes nothing)',
          T.MODE == 'off')


def test_signature(main):
    print('\nsignature against %s' % main)
    import nxmap
    img = bytearray(nxmap.Main(str(main)).img)
    check('verify() is clean on the real module', not T.verify(bytes(img)),
          '; '.join(T.verify(bytes(img))))
    check('read_state says stock', T.read_state(bytes(img)) == 'off')

    caught = 0
    for va, _want, _what in T.ANCHORS:
        keep = bytes(img[va:va + 4])
        struct.pack_into('<I', img, va, 0)
        if T.verify(bytes(img)):
            caught += 1
        img[va:va + 4] = keep
    check('all %d anchors are load-bearing' % len(T.ANCHORS),
          caught == len(T.ANCHORS), '%d of %d' % (caught, len(T.ANCHORS)))

    # A wrong-but-plausible word at the gate must be refused, not adopted.
    keep = bytes(img[T.GATE_VA:T.GATE_VA + 4])
    struct.pack_into('<I', img, T.GATE_VA, 0xD503201F)          # nop
    check('a NOP at the gate is refused', bool(T.verify(bytes(img))))
    struct.pack_into('<I', img, T.GATE_VA, T.GATE_NOCACHE)
    check('the patched word is accepted (re-runnable over its own output)',
          not T.verify(bytes(img)))
    check('read_state says nocache once patched',
          T.read_state(bytes(img)) == 'nocache')
    img[T.GATE_VA:T.GATE_VA + 4] = keep


def test_destroy(main):
    """The branch we force must already be reachable, and must be complete.

    This is the group that distinguishes "one small word" from "one small
    word that leaks the texture instead of the surface".
    """
    print('\nthe destroy path we force')
    import nxmap
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    img = nxmap.Main(str(main)).img
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    ins = list(md.disasm(bytes(img[T.DESTROY_VA:0x4438]), T.DESTROY_VA))
    text = [(i.address, i.mnemonic, i.op_str) for i in ins]

    check('stock reaches it whenever a key holds ten',
          _target(T.GATE_STOCK, T.GATE_VA) == T.DESTROY_VA)
    # Two virtual destructor calls, one per surface.
    blrs = [t for t in text if t[1] == 'blr']
    check('it makes two virtual calls (one destructor per surface)',
          len(blrs) == 2, repr(blrs))
    # Both surface pointers nulled.
    strs = [t for t in text if t[1] == 'str' and 'xzr' in t[2]]
    check('it nulls both surface pointers and the slot',
          len(strs) >= 3, repr(strs))
    check('the slot itself is cleared',
          any(t[0] == 0x4430 and t[1] == 'str' and 'xzr' in t[2]
              for t in text), repr(text[-4:]))
    # And the container is freed.
    bls = [t for t in text if t[1] == 'bl']
    check('it frees the container', len(bls) == 1, repr(bls))
    check('it returns success', any(t[1] == 'mov' and t[2].startswith('w0, #1')
                                    for t in text))


def test_write(main):
    print('\nwrite / diff / round trip')
    import nxmap
    stock = nxmap.Main(str(main)).img
    if T.read_state(stock) != 'off':
        skip('write', 'the source module is not stock at this gate')
        return
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, 'main.nocache')
        check('apply nocache',
              T.apply_to_nso(main, out, lambda *_: None, 'nocache'))
        new = nxmap.Main(out).img
        check('images are the same length', len(new) == len(stock))
        diff = [i for i in range(0, min(len(new), len(stock)), 4)
                if new[i:i + 4] != stock[i:i + 4]]
        check('exactly ONE word changed', diff == [T.GATE_VA],
              [hex(d) for d in diff])
        check('and it is the gate', T.read_state(new) == 'nocache')
        check('it still verifies', not T.verify(new))
        check('re-running writes nothing',
              T.apply_to_nso(out, os.path.join(td, 'x'),
                             lambda *_: None, 'nocache') is False)
        back = os.path.join(td, 'main.back')
        check('round trip back to stock wrote a module',
              T.apply_to_nso(out, back, lambda *_: None, 'off'))
        check('round trip restores the original image exactly',
              bytes(nxmap.Main(back).img) == bytes(stock))


def test_wiring():
    print('\nbuild wiring')
    src = (HERE / 'build.py').read_text(encoding='utf-8')
    gui = (HERE / '7th_heaven_nx.py').read_text(encoding='utf-8')
    check('apply_texcache exists', 'def apply_texcache(' in src)
    check('it imports the module', 'import ff7nx_texcache' in src)
    check('it refuses to clobber a foreign module',
          src.count('already holds a module this build did not produce') >= 3)
    check('the GUI calls it', 'build.apply_texcache(' in gui)
    # Order matters: whoever writes exefs/main last must see the rest.
    check('it runs AFTER apply_glerror',
          gui.index('build.apply_texcache(') > gui.index('build.apply_glerror('))
    check('the GUI env name matches the module',
          "_HEADLESS_TEXCACHE_ENV = '%s'" % T.MODE_ENV in gui)
    check('there is a checkbox bound to it', 'texcache_var' in gui)
    check('the setting is saved', "'no_texture_cache'" in gui)
    check('and restored headlessly',
          gui.count('_HEADLESS_TEXCACHE_ENV') >= 3)


def main_(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--main')
    a = ap.parse_args(argv)

    print('== the texture cache')
    test_table()
    test_mode()
    test_wiring()
    m = find_main(a.main)
    if m is None:
        skip('signature / destroy / write', 'no exefs/main found')
    else:
        test_signature(m)
        test_destroy(m)
        test_write(m)

    print('')
    if FAILURES:
        print('FAILED (%d):' % len(FAILURES))
        for f in FAILURES:
            print('  - %s' % f)
        return 1
    print('all checks passed%s'
          % (' (%d skipped)' % len(SKIPPED) if SKIPPED else ''))
    return 0


if __name__ == '__main__':
    raise SystemExit(main_())
