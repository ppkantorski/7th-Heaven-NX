#!/usr/bin/env python3
"""
test_gfxpool.py -- the graphics-pool patch, against the real module.

Unit checks run anywhere. The checks that matter -- the ones that prove the
hook lands on the right instruction and that a patched module differs from
its source in EXACTLY two words -- need `exefs/main`, and they are SKIPPED
rather than failed when no dump is present.

    python3 test_gfxpool.py
    python3 test_gfxpool.py --main <some other exefs/main>

WHAT EACH GROUP IS FOR
----------------------
1. encoder      -- the module must reproduce the port's own two words before
                   it is allowed to write different ones.
2. settings     -- pool_mb() reads the environment. A value nobody can parse
                   must read back as STOCK, never as the default: a setting
                   that cannot be read is not permission to change a memory
                   reservation.
3. signature    -- verify_site() against the real module, and against seven
                   single-word mutations of it. A signature that passes on a
                   module it should not is worse than no signature.
4. write        -- patch a real module in memory and diff it. Two words,
                   both ours, everything else byte-identical, and a
                   round-trip back to stock that restores the exact bytes.
5. build wiring -- apply_gfxpool exists, is called by the GUI's build order,
                   and is a no-op at stock.
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

import ff7nx_gfxpool as G                                      # noqa: E402

MB = G.MB

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
    for rel in ('game_data_files/exefs/main',
                'dump/exefs/main',
                'sdout/atmosphere/contents/0100A5B00BDC6000/exefs/main'):
        p = HERE / rel
        if p.is_file():
            return p
    return None


# --------------------------------------------------------------- 1. encoder
def test_encoder():
    print('\nencoder')
    check('selftest', G.selftest(lambda *_: None))
    # The stock words, spelled out here independently of the module's own
    # SITE table so a bad edit to that table cannot make this pass.
    check('mov w0, #0x10000000 == 0x320403E0',
          G.encode_size(0, 256 * MB) == 0x320403E0,
          '%08X' % G.encode_size(0, 256 * MB))
    check('mov w1, #0x10000000 == 0x320403E1',
          G.encode_size(1, 256 * MB) == 0x320403E1,
          '%08X' % G.encode_size(1, 256 * MB))
    # 384 MB is 0x18000000 -- two contiguous bits, so it is a legal logical
    # immediate and must come out as ORR rather than MOVZ.
    w = G.encode_size(0, 384 * MB)
    check('384 MB encodes as ORR (0x32......)', (w >> 24) == 0x32,
          '%08X' % w)
    check('384 MB decodes back', G.decode_size(w, 0) == 384 * MB)
    # A size that is NOT a contiguous run must fall back to MOVZ and still
    # round-trip. 320 MB = 0x14000000 is exactly that case.
    w = G.encode_size(1, 320 * MB)
    check('320 MB encodes as MOVZ (0x52A.....)',
          (w & 0xFFE00000) == 0x52A00000, '%08X' % w)
    check('320 MB decodes back', G.decode_size(w, 1) == 320 * MB)
    check('decode rejects the wrong register',
          G.decode_size(G.encode_size(0, 384 * MB), 1) is None)
    check('decode rejects a non-mov word', G.decode_size(0xD503201F, 0) is None)
    for mb in G.sizes():
        for rd in (0, 1):
            if not check('round trip %d MB w%d' % (mb, rd),
                         G.decode_size(G.encode_size(rd, mb * MB), rd)
                         == mb * MB):
                return


def test_encodable():
    print('\nencodable / sizes')
    check('stock is writable (it is how --stock works)',
          G.encodable(G.STOCK_MB) is None)
    check('384 is writable', G.encodable(384) is None)
    # Below stock is WRITABLE on purpose -- it is the used-extent A/B, and a
    # test that forbids it forbids the only experiment that separates
    # "the high-water mark crossed something" from "the pool ran out".
    check('192 is writable (the diagnostic rung)', G.encodable(192) is None)
    check('MIN_MB is writable', G.encodable(G.MIN_MB) is None)
    check('64 refused (under MIN_MB)', G.encodable(64) is not None)
    check('1024 refused (over MAX_MB)', G.encodable(1024) is not None)
    check('0 refused', G.encodable(0) is not None)
    check('-256 refused', G.encodable(-256) is not None)
    check('True refused (bool is not a size)', G.encodable(True) is not None)
    check('257 refused (not a whole 64 KB multiple? it is -- must PASS)',
          G.encodable(257) is None)
    check('sizes() is ascending and starts at MIN_MB',
          G.sizes() == sorted(G.sizes()) and G.sizes()[0] == G.MIN_MB,
          repr(G.sizes()))
    check('stock is on the ladder', G.STOCK_MB in G.sizes())
    check('MAX_MB is in sizes()', G.MAX_MB in G.sizes())
    check('the DEFAULT is still stock -- a plain build writes nothing',
          G.DEFAULT_MB == G.STOCK_MB)


# -------------------------------------------------------------- 2. settings
def test_pool_mb():
    print('\npool_mb() from the environment')
    E = G.POOL_MB_ENV
    check('unset -> DEFAULT_MB', G.pool_mb({}) == G.DEFAULT_MB)
    check('empty -> DEFAULT_MB', G.pool_mb({E: ''}) == G.DEFAULT_MB)
    check('"384" -> 384', G.pool_mb({E: '384'}) == 384)
    check('" 512 " -> 512', G.pool_mb({E: ' 512 '}) == 512)
    check('"0" -> stock (explicit off)', G.pool_mb({E: '0'}) == G.STOCK_MB)
    check('"256" -> stock', G.pool_mb({E: '256'}) == G.STOCK_MB)
    # The important ones. A setting that cannot be understood must not
    # become the default -- it must become "change nothing".
    check('garbage -> stock, NOT the default',
          G.pool_mb({E: 'lots'}) == G.STOCK_MB)
    check('over the ceiling -> stock, NOT clamped',
          G.pool_mb({E: '4096'}) == G.STOCK_MB)
    check('under the floor -> stock, NOT clamped',
          G.pool_mb({E: '64'}) == G.STOCK_MB)
    check('"192" -> 192 (the diagnostic rung reaches the build)',
          G.pool_mb({E: '192'}) == 192)
    check('float string -> stock', G.pool_mb({E: '384.5'}) == G.STOCK_MB)


# ------------------------------------------------------------- 3. signature
def test_signature(main):
    print('\nsignature against %s' % main)
    import nxmap
    img = bytearray(nxmap.Main(str(main)).img)
    bad = G.verify_site(bytes(img))
    check('verify_site is clean on the real module', not bad,
          '; '.join(bad))
    check('read_mb decodes a whole number of MB',
          G.read_mb(bytes(img)) is not None, repr(G.read_mb(bytes(img))))

    # MUTATION CHECK. Break each signature word in turn; the signature must
    # notice every one. Without this, "verify passes" only proves the file
    # was readable.
    caught = 0
    for i in range(len(G.SITE['words'])):
        va = G.SITE['va'] + 4 * i
        keep = bytes(img[va:va + 4])
        struct.pack_into('<I', img, va, 0xD503201F)             # nop
        if G.verify_site(bytes(img)):
            caught += 1
        else:
            check('mutation at +0x%07X is caught' % va, False,
                  'signature still passed with that word NOPed')
        img[va:va + 4] = keep
    check('all %d signature words are load-bearing'
          % len(G.SITE['words']), caught == len(G.SITE['words']),
          '%d of %d' % (caught, len(G.SITE['words'])))

    # The two SIZE words must be accepted at ANY whole-MB value -- that is
    # what makes the patch re-runnable over its own output -- but rejected
    # when they are not a `mov Wd, #imm` at all.
    for mb in (256, 384, 512):
        for i, (rd, _what) in sorted(G.SITE['fields'].items()):
            va = G.SITE['va'] + 4 * i
            keep = bytes(img[va:va + 4])
            struct.pack_into('<I', img, va, G.encode_size(rd, mb * MB))
            ok = not G.verify_site(bytes(img))
            img[va:va + 4] = keep
            if not check('signature accepts %d MB at +0x%07X' % (mb, va), ok):
                return


# ----------------------------------------------------------------- 4. write
def test_write(main):
    print('\nwrite / diff / round trip')
    import nxmap
    stock_img = nxmap.Main(str(main)).img
    live = G.read_mb(stock_img)
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, 'main')
        ok = G.apply_to_nso(main, out, lambda *_: None, 384)
        if live == 384:
            skip('write 384 MB', 'the source module is already at 384 MB')
            return
        if not check('apply_to_nso wrote a module', ok and
                     os.path.exists(out)):
            return
        new_img = nxmap.Main(out).img
        check('decompressed images are the same length',
              len(new_img) == len(stock_img))
        diff = [i for i in range(0, min(len(new_img), len(stock_img)), 4)
                if new_img[i:i + 4] != stock_img[i:i + 4]]
        want = sorted(G.SITE['va'] + 4 * i for i in G.SITE['fields'])
        check('exactly the two intended words changed',
              sorted(diff) == want,
              'changed %s, wanted %s' % ([hex(d) for d in diff],
                                         [hex(w) for w in want]))
        check('the module now reads 384 MB', G.read_mb(new_img) == 384)
        check('verify_site still clean after the write',
              not G.verify_site(new_img))

        # Re-running at the same size must be a no-op, not a second edit.
        again = os.path.join(td, 'main2')
        check('re-running at 384 writes nothing',
              G.apply_to_nso(out, again, lambda *_: None, 384) is False)

        # And the way back. Byte-for-byte on the DECOMPRESSED image; the
        # file itself may differ because the segment is re-LZ4'd.
        back = os.path.join(td, 'main3')
        check('round trip back to stock wrote a module',
              G.apply_to_nso(out, back, lambda *_: None, G.STOCK_MB))
        if os.path.exists(back):
            check('round trip restores the original image exactly',
                  nxmap.Main(back).img == stock_img)


# ----------------------------------------------------------- 5. build wiring
def test_wiring():
    print('\nbuild / GUI wiring')
    src = (HERE / 'build.py').read_text(encoding='utf-8')
    check('build.apply_gfxpool exists', 'def apply_gfxpool(' in src)
    check('build.apply_gfxpool imports the module',
          'import ff7nx_gfxpool' in src)
    gui = (HERE / '7th_heaven_nx.py').read_text(encoding='utf-8')
    check('the GUI build order calls it',
          'build.apply_gfxpool(SDOUT_DIR, DUMP, log, produced)' in gui)
    check('it runs AFTER apply_heap (both edit exefs/main)',
          gui.index('build.apply_gfxpool(') > gui.index('build.apply_heap('))
    check('the GUI has a graphics-memory dropdown',
          'GFX_POOL_CHOICES' in gui and "'Graphics memory'" in gui)
    check('the setting is persisted', "'gfx_pool_mb'" in gui)
    check('the setting is written to the environment',
          '_HEADLESS_GFX_ENV] = str(current_gfx_pool())' in gui)
    check('a headless run picks it up from settings.json',
          '_HEADLESS_GFX_ENV' in gui)
    check('the GUI env name matches the module',
          "_HEADLESS_GFX_ENV = '%s'" % G.POOL_MB_ENV in gui)
    # Every value the dropdown offers must be one the module will write.
    import re
    offered = [int(m) for m in re.findall(r'^\s*\((\d+), .\d+ MB',
                                          gui, re.M)]
    check('the dropdown offers at least the whole ladder',
          set(G.sizes()) <= set(offered), 'offered %r' % offered)
    check('every dropdown value is writable',
          all(G.encodable(v) is None for v in offered),
          repr([v for v in offered if G.encodable(v) is not None]))


def test_stock_is_a_noop(main):
    print('\nstock is a no-op')
    import nxmap
    img = nxmap.Main(str(main)).img
    if G.read_mb(img) != G.STOCK_MB:
        skip('patches() at stock is empty',
             'this module is not at stock (%s MB)' % G.read_mb(img))
        return
    check('patches() at stock is empty', G.patches(img, G.STOCK_MB) == [])
    check('spec() at stock is None', G.spec(img, G.STOCK_MB) is None)


def main_(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--main', help='an exefs/main to test against')
    a = ap.parse_args(argv)

    print('== ff7nx_gfxpool')
    test_encoder()
    test_encodable()
    test_pool_mb()
    test_wiring()

    module = find_main(a.main)
    if module is None:
        skip('signature / write / stock', 'no exefs/main found')
    else:
        try:
            import nxmap                                       # noqa: F401
        except SystemExit as exc:
            skip('signature / write / stock', str(exc))
        else:
            test_signature(module)
            test_stock_is_a_noop(module)
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
