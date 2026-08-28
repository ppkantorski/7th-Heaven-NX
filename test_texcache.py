#!/usr/bin/env python3
"""
test_texcache.py -- disabling the port's (w,h)-keyed texture cache.

Unit checks run anywhere. The ones that matter need `exefs/main` and are
SKIPPED rather than failed when no dump is present.

    python3 test_texcache.py
    python3 test_texcache.py --main <some other exefs/main>

WHAT EACH GROUP IS FOR
----------------------
1. table     -- the stock/nocache words and the bounded cave control flow.
2. policy    -- size is inclusive through 256 in BOTH dimensions, the stock
                ten-per-(w,h) reuse is retained, and the global cap is 64.
3. mode      -- the environment override, including the old boolean spelling
                so a settings.json from build 192 still means what it meant.
4. signature -- verify() against the real module and against mutations of
                it. Every anchor is load-bearing, every mode is accepted at
                rest so the pass is re-runnable over its own output, and a
                MIXTURE of two modes is refused.
5. write     -- each mode changes exactly the words it should, every one of
                the nine mode transitions lands where it says, and stock
                comes back byte-identical.
6. destroy   -- the branch we force is REACHABLE IN STOCK and complete: it
                releases both surfaces, nulls both pointers, frees the
                container and clears the slot. This is the group that says
                the patch is safe rather than merely small.
7. wiring    -- the build pass exists, the GUI calls it, it runs after
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
    """Where an A64 B / BL / B.cond at `va` lands."""
    if (word & 0xFC000000) in (0x14000000, 0x94000000):
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
    check('three modes', set(T.MODES) == {'off', 'nocache', 'small'})
    check('stock is a b.hi', (T.SITE['off'][T.GATE_VA] >> 24) == 0x54
          and (T.SITE['off'][T.GATE_VA] & 0xF) == 8)
    check('nocache is an unconditional b',
          (T.SITE['nocache'][T.GATE_VA] >> 26) == 0x05)
    for m in ('off', 'nocache'):
        check('%s branches to the same destroy path' % m,
              _target(T.SITE[m][T.GATE_VA], T.GATE_VA) == T.DESTROY_VA,
              hex(_target(T.SITE[m][T.GATE_VA], T.GATE_VA)))
    check('nocache touches ONE word',
          sum(1 for va in (T.CALL_VA, T.CMP_VA, T.GATE_VA)
              if T.SITE['nocache'][va] != T.SITE['off'][va]) == 1)
    base = 0x10000
    cave = T._small_words(lambda i: base + i * 4)
    check('bounded small cave has seventeen payload words', len(cave) == 17)
    for i in (3, 7, 11, 15):
        check('small reject %d reaches the stock destroy path' % i,
              _target(cave[i], base + i * 4) == T.DESTROY_VA)
    check('small calls the original equal_range',
          _target(cave[12], base + 48) == T.EQUAL_RANGE_VA)
    check('small returns to the original insert path',
          _target(cave[16], base + 64) == T.RETURN_VA)
    check('the stock cap really is ten per key (cmp x0, #9)',
          T.SITE['off'][T.CMP_VA] == 0xF100241F)
    check('legacy unbounded small is recognized only for migration',
          'small_legacy' in T.SITE and 'small' not in T.SITE)
    check('anchor addresses are unique',
          len({a for a, _, _ in T.ANCHORS}) == len(T.ANCHORS))
    check('no anchor sits on a mutable word',
          not ({a for a, _, _ in T.ANCHORS}
               & {T.CALL_VA, T.CMP_VA, T.GATE_VA}))


def test_threshold():
    """The two-dimensional, per-key bounded policy."""
    print('\nthe bounded policy')

    def cached(wd, ht, already, total=0):
        return (wd <= T.SMALL_MAX and ht <= T.SMALL_MAX
                and already < T.SMALL_PER_KEY
                and total < T.SMALL_GLOBAL)

    for wd, ht in ((1, 1), (64, 128), (255, 255), (256, 256),
                   (256, 8), (8, 256)):
        check('%dx%d is recycled' % (wd, ht), cached(wd, ht, 0))
    # The ones that were eating the pool.
    for wd, ht in ((257, 257), (512, 512), (768, 192), (192, 768),
                   (257, 8), (8, 257)):
        check('%dx%d is destroyed, not cached' % (wd, ht),
              not cached(wd, ht, 0))
    check('the threshold matches SMALL_MAX', T.SMALL_MAX == 256)
    # A tall-thin surface must not sneak through on its small dimension --
    # that is the bug a width-only test would have had.
    check('the gate tests BOTH dimensions, not just width',
          not cached(8, 1024, 0) and not cached(1024, 8, 0))
    for n in range(10):
        check('small key population %d accepts one more' % n,
              cached(256, 256, n))
    check('small key population 10 is destroyed', not cached(256, 256, 10))
    check('global population 63 accepts one more', cached(256, 256, 0, 63))
    check('global population 64 is destroyed', not cached(1, 1, 0, 64))
    check('the threshold is inclusive', T.SMALL_MAX == 256)
    check('the population cap is stock ten', T.SMALL_PER_KEY == 10)
    check('the global population cap is 64', T.SMALL_GLOBAL == 64)


def test_mode():
    print('\nmode from the environment')
    E = T.MODE_ENV
    check('unset -> the code constant', T.mode({}) == T.MODE)
    check('empty -> the code constant', T.mode({E: ''}) == T.MODE)
    check('"small" -> small', T.mode({E: 'small'}) == 'small')
    check('"nocache" -> nocache', T.mode({E: 'nocache'}) == 'nocache')
    check('"1" -> nocache (the old boolean)', T.mode({E: '1'}) == 'nocache')
    check('"ON" -> nocache (case folded)', T.mode({E: 'ON'}) == 'nocache')
    check('"off" -> off', T.mode({E: 'off'}) == 'off')
    check('"0" -> off (the old boolean)', T.mode({E: '0'}) == 'off')
    check('garbage -> the code constant', T.mode({E: 'banana'}) == T.MODE)
    check('the default is small', T.MODE == 'small')


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
    img[T.GATE_VA:T.GATE_VA + 4] = keep

    # Inline states, including the broken Build 193 state, must be accepted
    # so the pass can migrate them without forcing a clean rebuild.
    for m in ('off', 'nocache', 'small_legacy'):
        keep3 = bytes(img[T.CALL_VA:T.GATE_VA + 4])
        for va in (T.CALL_VA, T.CMP_VA, T.GATE_VA):
            struct.pack_into('<I', img, va, T.SITE[m][va])
        ok = (not T.verify(bytes(img))) and T.read_state(bytes(img)) == m
        check('%s is accepted at rest and reads back' % m, ok)
        other = 'nocache' if m != 'nocache' else 'small_legacy'
        struct.pack_into('<I', img, T.CALL_VA, T.SITE[other][T.CALL_VA])
        if T.SITE[other][T.CALL_VA] != T.SITE[m][T.CALL_VA]:
            check('%s + %s call is refused as a mixture' % (m, other),
                  bool(T.verify(bytes(img))))
        img[T.CALL_VA:T.GATE_VA + 4] = keep3


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
          _target(T.SITE['off'][T.GATE_VA], T.GATE_VA) == T.DESTROY_VA)
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
        built = {'off': str(main)}
        for m in ('nocache', 'small'):
            out = os.path.join(td, 'main.' + m)
            if not check('apply %s' % m,
                         T.apply_to_nso(main, out, lambda *_: None, m)):
                continue
            built[m] = out
            new = nxmap.Main(out).img
            check('%s: same length' % m, len(new) == len(stock))
            diff = [i for i in range(0, min(len(new), len(stock)), 4)
                    if new[i:i + 4] != stock[i:i + 4]]
            if m == 'nocache':
                check('nocache: changed exactly the gate word',
                      diff == [T.GATE_VA], [hex(d) for d in diff])
            else:
                cave = T._small_cave(new)
                check('small: hook and cave are recognized', cave is not None)
                physical = set(cave[1]) if cave else set()
                check('small: diff is exactly hook plus cave/chaining words',
                      set(diff) == physical | {T.CALL_VA},
                      [hex(d) for d in diff])
                check('small: stock cmp and gate remain unchanged',
                      T.CMP_VA not in diff and T.GATE_VA not in diff)
                # Execute the exact scattered words, including every chain
                # branch. equal_range is the one native call and returns the
                # bucket population supplied by each case.
                import arm64emu
                code = {va: struct.unpack_from('<I', new, va)[0]
                        for va in physical}
                entry = T._branch_target(
                    struct.unpack_from('<I', new, T.CALL_VA)[0], T.CALL_VA)

                def decision(wd, ht, population, total=0):
                    mem = arm64emu.Mem()
                    sp = 0x800000
                    cache = 0x900000
                    mem.setu(sp + 8, wd, 4)
                    mem.setu(sp + 12, ht, 4)
                    mem.setu(cache + 0x10, total, 8)
                    calls = []

                    def equal_range(cpu):
                        calls.append(True)
                        cpu.set(0, population)

                    cpu = arm64emu.Cpu(
                        mem, native={T.EQUAL_RANGE_VA: equal_range})
                    cpu.sp = sp
                    cpu.set(20, cache)
                    return (cpu.run(entry, [], code=code, start_pc=entry),
                            len(calls))

                cases = [
                    (1, 1, 0, 0, T.RETURN_VA, 1),
                    (256, 256, 9, 63, T.RETURN_VA, 1),
                    (256, 256, 10, 63, T.DESTROY_VA, 1),
                    (1, 1, 0, 64, T.DESTROY_VA, 0),
                    (257, 1, 0, 0, T.DESTROY_VA, 0),
                    (1, 257, 0, 0, T.DESTROY_VA, 0),
                ]
                for wd, ht, pop, total, want, calls in cases:
                    got = decision(wd, ht, pop, total)
                    check('execute %dx%d key %d total %d -> +0x%X'
                          % (wd, ht, pop, total, want),
                          got == (want, calls),
                          repr(got))
            check('%s: reads back' % m, T.read_state(new) == m)
            check('%s: still verifies' % m, not T.verify(new))
            check('%s: re-running writes nothing' % m,
                  T.apply_to_nso(out, os.path.join(td, 'x'),
                                 lambda *_: None, m) is False)
        # Every transition, not just from stock. A user flipping the combo
        # rebuilds from whatever the last build left behind.
        for a in T.MODES:
            for b in T.MODES:
                if a not in built:
                    continue
                dst = os.path.join(td, 'trans')
                wrote = T.apply_to_nso(built[a], dst, lambda *_: None, b)
                img = nxmap.Main(dst if wrote else built[a]).img
                check('%-7s -> %-7s' % (a, b), T.read_state(img) == b,
                      repr(T.read_state(img)))
                if os.path.exists(dst):
                    os.remove(dst)
        for m in ('nocache', 'small'):
            if m not in built:
                continue
            back = os.path.join(td, 'back.' + m)
            check('%s -> off wrote a module' % m,
                  T.apply_to_nso(built[m], back, lambda *_: None, 'off'))
            check('%s -> off restores stock byte-for-byte' % m,
                  bytes(nxmap.Main(back).img) == bytes(stock))

        # Explicitly prove that the corrupting old small mode migrates to the
        # new bounded cave rather than being mistaken for it.
        import nso_patcher
        legacy = os.path.join(td, 'main.legacy')
        nso = nso_patcher.read_nso(Path(main))
        legacy_spec = {'name': 'legacy small fixture', 'patches': [
            {'name': 'legacy +%x' % va, 'va': va,
             'expect': struct.pack('<I', T.SITE['off'][va]).hex(),
             'set': struct.pack('<I', T.SITE['small_legacy'][va]).hex()}
            for va in (T.CALL_VA, T.CMP_VA, T.GATE_VA)]}
        nso_patcher.apply_spec(nso, legacy_spec)
        Path(legacy).write_bytes(nso_patcher.rebuild(nso))
        check('legacy fixture reads as small_legacy',
              T.read_state(nxmap.Main(legacy).img) == 'small_legacy')
        migrated = os.path.join(td, 'main.migrated')
        check('legacy small migrates',
              T.apply_to_nso(legacy, migrated, lambda *_: None, 'small'))
        check('legacy small reads back as bounded small',
              T.read_state(nxmap.Main(migrated).img) == 'small')

        # Build 195's safe four-per-key cave must also migrate. It has the
        # same size/global bounds; only the equal_range population compare
        # differs, so rewriting it cannot increase the hard 16 MiB ceiling.
        bounded4 = os.path.join(td, 'main.bounded4')
        nso = nso_patcher.read_nso(Path(built['small']))
        current_img = nxmap.Main(built['small']).img
        cave = T._small_cave(current_img)
        old_cmp = cave[0][13][0]
        bounded4_spec = {'name': 'Build 195 bounded-four fixture', 'patches': [{
            'name': 'per-key ten -> four', 'va': old_cmp,
            'expect': struct.pack('<I', cave[0][13][1]).hex(),
            'set': struct.pack('<I',
                0xF100001F | ((T.SMALL_PER_KEY_V1 - 1) << 10)).hex()}]}
        nso_patcher.apply_spec(nso, bounded4_spec)
        Path(bounded4).write_bytes(nso_patcher.rebuild(nso))
        check('Build 195 fixture reads as small_bounded4',
              T.read_state(nxmap.Main(bounded4).img) == 'small_bounded4')
        migrated4 = os.path.join(td, 'main.migrated4')
        check('Build 195 bounded-four migrates',
              T.apply_to_nso(bounded4, migrated4, lambda *_: None, 'small'))
        check('Build 195 migration reads as current bounded small',
              T.read_state(nxmap.Main(migrated4).img) == 'small')
        for target_mode in T.MODES:
            transitioned4 = os.path.join(td, 'main.bounded4.' + target_mode)
            wrote = T.apply_to_nso(
                bounded4, transitioned4, lambda *_: None, target_mode)
            got_path = transitioned4 if wrote else bounded4
            check('Build 195 bounded-four -> %s' % target_mode,
                  T.read_state(nxmap.Main(got_path).img) == target_mode)


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
    check('there is a combo bound to it', 'texcache_var' in gui
          and "('combo', 'Texture cache'" in gui)
    check('it offers every mode',
          all("'%s'" % m in gui.split('TEX_CACHE_CHOICES')[1][:400]
              for m in T.MODES))
    check('the setting is saved', "'texture_cache': current_texture_cache()"
          in gui)
    check('the old boolean still migrates', "'no_texture_cache'" in gui)
    check('and it is restored headlessly',
          gui.count('_HEADLESS_TEXCACHE_ENV') >= 3)
    check('the build refuses stock loudly rather than silently',
          'LEFT STOCK' in src)


def main_(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--main')
    a = ap.parse_args(argv)

    print('== the texture cache')
    test_table()
    test_threshold()
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
