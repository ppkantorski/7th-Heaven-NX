#!/usr/bin/env python3
"""
test_glerror.py -- the OpenGL error reporter removal, against the real module.

Unit checks run anywhere. The ones that matter need `exefs/main` and are
SKIPPED rather than failed when no dump is present.

    python3 test_glerror.py
    python3 test_glerror.py --main <some other exefs/main>

WHAT EACH GROUP IS FOR
----------------------
1. table     -- thirteen uniquely fingerprinted gates and reporter traps.
2. safety    -- the first check, fatal word, second check, and error-drain
                back-edge all have to be present before anything is written.
3. signature -- verify() against the real module and against mutations of
                it: every gate and fatal/drain sequence is load-bearing.
4. write     -- exactly the selected fatal words change; legacy gate skips
                are migrated back to stock; every mode round-trips.
5. wiring    -- the build pass exists, the GUI calls it, and it runs last.
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

import ff7nx_glerror as G                                      # noqa: E402

FAILURES: list[str] = []
SKIPPED: list[str] = []


def check(label, ok, detail=''):
    print('  %-56s %s%s' % (label, 'ok' if ok else 'FAIL',
                            ('  -- ' + detail) if detail and not ok else ''))
    if not ok:
        FAILURES.append(label + (('  ' + detail) if detail else ''))
    return ok


def skip(label, why):
    print('  %-56s skipped  -- %s' % (label, why))
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


def test_table():
    print('\ntable')
    check('selftest', G.selftest(lambda *_: None))
    check('13 gates', len(G.GATES) == 13, str(len(G.GATES)))
    check('addresses are unique',
          len({g[0] for g in G.GATES}) == len(G.GATES))
    # A BL encodes its own displacement, so two gates sharing a stock word
    # would mean the table was filled in by hand and got it wrong.
    check('stock words are unique (each BL encodes its own displacement)',
          len({g[1] for g in G.GATES}) == len(G.GATES))
    check('the three flip gates are in the table',
          set(G.FLIP_VAS) <= {g[0] for g in G.GATES})
    check('13 reporter traps', len(G.REPORT_TRAPS) == 13)
    check('every gate has one reporter trap',
          set(G.REPORT_TRAPS) == {g[0] for g in G.GATES})
    check('reporter trap addresses are unique',
          len(set(G.REPORT_TRAPS.values())) == len(G.GATES))
    check('flip is a strict subset of all',
          set(G.FLIP_VAS) < {g[0] for g in G.GATES})
    check('mode off selects nothing', G.gates_for('off') == [])


def test_mode():
    print('\nmode from the environment')
    E = G.MODE_ENV
    check('unset -> the code constant', G.mode({}) == G.MODE)
    check('empty -> the code constant', G.mode({E: ''}) == G.MODE)
    check('"off" -> off', G.mode({E: 'off'}) == 'off')
    check('"flip" -> flip', G.mode({E: 'flip'}) == 'flip')
    check('"ALL" -> all (case folded)', G.mode({E: 'ALL'}) == 'all')
    check('garbage -> the code constant, not off',
          G.mode({E: 'banana'}) == G.MODE)
    check('the default is not off', G.MODE != 'off')


def test_safety():
    print('\nsafety predicates')
    check('cbz  w0, +0x5c  accepted', G.is_cbz_w0(0x340002E0))
    check('cbz  w1  rejected', not G.is_cbz_w0(0x340002E1))
    check('cbz  w23 rejected', not G.is_cbz_w0(0x340002F7))
    check('cbnz w0  rejected', not G.is_cbz_w0(0x350002E0))
    check('cbz  x0  rejected (64-bit form)', not G.is_cbz_w0(0xB40002E0))
    check('a nop is rejected', not G.is_cbz_w0(0xD503201F))
    check('drain cbnz w0 accepted', G.is_cbnz_w0(0x350002E0))
    check('drain cbnz w1 rejected', not G.is_cbnz_w0(0x350002E1))
    check('legacy quiet word identified', G.LEGACY_QUIET == 0x2A1F03E0)
    check('fatal word identified', G.FATAL == 0xE7FFDEFE)
    check('replacement is an A64 NOP', G.NOP == 0xD503201F)


def test_signature(main):
    print('\nsignature against %s' % main)
    import nxmap
    img = bytearray(nxmap.Main(str(main)).img)
    check('verify() is clean on the real module', not G.verify(bytes(img)),
          '; '.join(G.verify(bytes(img))))
    st = G.read_state(bytes(img))
    check('read_state decodes', st is not None, repr(st))

    # MUTATION. Break every word that anchors the gate and reporter drain.
    caught = 0
    total = 0
    for va, _stock, _before, _name in G.GATES:
        trap = G.REPORT_TRAPS[va]
        for site in (va - 4, va, va + 4, trap, trap + 4, trap + 12):
            total += 1
            keep = bytes(img[site:site + 4])
            # Use zero so mutating the fatal site does not accidentally use
            # its other accepted resting word (NOP).
            struct.pack_into('<I', img, site, 0)
            if G.verify(bytes(img)):
                caught += 1
            img[site:site + 4] = keep
    check('all %d signature words are load-bearing' % total, caught == total,
          '%d of %d' % (caught, total))

    # The gate must be accepted in BOTH resting states, or the patch is not
    # re-runnable over its own output.
    for va, stock, _b, _n in G.GATES:
        keep = bytes(img[va:va + 4])
        struct.pack_into('<I', img, va, G.LEGACY_QUIET)
        ok = not G.verify(bytes(img))
        img[va:va + 4] = keep
        if not check('gate +0x%07X accepts legacy state for migration' % va,
                     ok):
            return
        trap = G.REPORT_TRAPS[va]
        keep = bytes(img[trap:trap + 4])
        struct.pack_into('<I', img, trap, G.NOP)
        ok = not G.verify(bytes(img))
        img[trap:trap + 4] = keep
        if not check('trap +0x%07X accepts already non-fatal' % trap, ok):
            return


def test_write(main):
    print('\nwrite / diff / round trip')
    import nxmap
    stock = nxmap.Main(str(main)).img
    if G.read_state(stock) != (0, len(G.GATES)):
        skip('write', 'the source module is not fully stock at these gates')
        return
    with tempfile.TemporaryDirectory() as td:
        for m, n in (('flip', 3), ('all', 13)):
            out = os.path.join(td, 'main.' + m)
            if not check('apply mode %s' % m,
                         G.apply_to_nso(main, out, lambda *_: None, m)):
                continue
            new = nxmap.Main(out).img
            diff = [i for i in range(0, min(len(new), len(stock)), 4)
                    if new[i:i + 4] != stock[i:i + 4]]
            want = sorted(G.REPORT_TRAPS[g[0]] for g in G.gates_for(m))
            check('mode %s changed exactly %d word(s)' % (m, n),
                  sorted(diff) == want,
                  'changed %s' % [hex(d) for d in diff])
            check('mode %s reads back as %d non-fatal' % (m, n),
                  G.read_state(new) == (n, len(G.GATES)))
            check('mode %s still verifies' % m, not G.verify(new))
            check('re-running mode %s writes nothing' % m,
                  G.apply_to_nso(out, os.path.join(td, 'x'),
                                 lambda *_: None, m) is False)
            back = os.path.join(td, 'back.' + m)
            check('mode %s -> off wrote a module' % m,
                  G.apply_to_nso(out, back, lambda *_: None, 'off'))
            if os.path.exists(back):
                check('mode %s -> off restores the original exactly' % m,
                      nxmap.Main(back).img == stock)
        # all -> flip must REMOVE ten, not add
        a = os.path.join(td, 'main.all')
        f2 = os.path.join(td, 'all2flip')
        if os.path.exists(a):
            check('all -> flip narrows back to 3',
                  G.apply_to_nso(a, f2, lambda *_: None, 'flip')
                  and G.read_state(nxmap.Main(f2).img) == (3, len(G.GATES)))

        # A build-188 module has three glGetError calls replaced by MOVs.
        # Applying the corrected mode must restore those three calls and NOP
        # their three fatal traps -- six intentional words, nothing else.
        legacy = bytearray(stock)
        for va in G.FLIP_VAS:
            struct.pack_into('<I', legacy, va, G.LEGACY_QUIET)
        legacy_nso = os.path.join(td, 'main.legacy')
        # Build a real compressed NSO through the same patcher used in prod.
        import nso_patcher
        n = nso_patcher.read_nso(main)
        ps = [{'name': 'legacy', 'va': va,
               'expect': G._hex(stock[va] | (stock[va + 1] << 8)
                                | (stock[va + 2] << 16)
                                | (stock[va + 3] << 24)),
               'set': G._hex(G.LEGACY_QUIET)} for va in G.FLIP_VAS]
        nso_patcher.apply_spec(n, {'name': 'legacy fixture', 'patches': ps})
        Path(legacy_nso).write_bytes(nso_patcher.rebuild(n))
        migrated = os.path.join(td, 'main.migrated')
        check('legacy flip module is migrated',
              G.apply_to_nso(legacy_nso, migrated, lambda *_: None, 'flip'))
        if os.path.exists(migrated):
            mig = nxmap.Main(migrated).img
            check('migration restores every glGetError gate',
                  all(struct.unpack_from('<I', mig, va)[0] == stock_word
                      for va, stock_word, _b, _n in G.GATES))
            check('migration reaches corrected flip state',
                  G.read_state(mig) == (3, len(G.GATES)))


def test_wiring():
    print('\nbuild / GUI wiring')
    src = (HERE / 'build.py').read_text(encoding='utf-8')
    check('build.apply_glerror exists', 'def apply_glerror(' in src)
    check('it imports the module', 'import ff7nx_glerror' in src)
    gui = (HERE / '7th_heaven_nx.py').read_text(encoding='utf-8')
    check('the GUI build order calls it',
          'build.apply_glerror(SDOUT_DIR, DUMP, log, produced)' in gui)
    check('it runs AFTER apply_heap',
          gui.index('build.apply_glerror(') > gui.index('build.apply_heap('))
    check('it runs AFTER apply_gfxpool',
          gui.index('build.apply_glerror(')
          > gui.index('build.apply_gfxpool('))
    # It edits exefs/main, so nothing that also edits exefs/main may follow.
    tail = gui[gui.index('build.apply_glerror('):]
    check('nothing else patches exefs/main after it',
          'apply_heap(' not in tail and 'apply_gfxpool(' not in tail
          and 'apply_field_frame(' not in tail)
    check('it is NOT a GUI setting (it is a defect fix)',
          'gl_error' not in gui.lower().replace('apply_glerror', ''))


def main_(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--main')
    a = ap.parse_args(argv)

    print('== ff7nx_glerror')
    test_table()
    test_mode()
    test_safety()
    test_wiring()

    module = find_main(a.main)
    if module is None:
        skip('signature / write', 'no exefs/main found')
    else:
        try:
            import nxmap                                       # noqa: F401
        except SystemExit as exc:
            skip('signature / write', str(exc))
        else:
            test_signature(module)
            test_write(module)

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
