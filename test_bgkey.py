#!/usr/bin/env python3
"""
test_bgkey.py -- self-tests for ff7nx_bgkey.py.

The synthetic tests need nothing. The archive tests need a flevel.lgp --
SEVENTH_NX_TEST_FLEVEL or --flevel -- and are skipped, loudly, without one.
Point it at the BUILT archive to check what actually shipped.

    python3 test_bgkey.py
    python3 test_bgkey.py --flevel sdout/.../workingdir/data/field/flevel.lgp
"""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ff7nx_bgkey as BK             # noqa: E402

FAILED = []


def check(name, cond, detail=''):
    print('  %-62s %s%s' % (name, 'ok' if cond else 'FAIL',
                            '' if cond else '   ' + detail))
    if not cond:
        FAILED.append(name)


# --------------------------------------------------------------------------
# a synthetic palette section, both header shapes
# --------------------------------------------------------------------------
def make_palette(pages=4, entry0=0x03E0, head=8):
    """
    A palette section body, as `lgp.split_sections` would hand it over.

    head=8  -> palX palY colours_per_page page_count
    head=12 -> the same behind an internal u32 length
    """
    out = bytearray()
    if head == 12:
        out += struct.pack('<I', 8 + 512 * pages)
    out += struct.pack('<HHHH', 0, 480, 256, pages)
    assert len(out) == head, (len(out), head)
    for p in range(pages):
        out += struct.pack('<H', entry0)
        for i in range(1, 256):
            out += struct.pack('<H', (i * 37 + p) & 0x7FFF)
    return bytes(out)


def test_parse():
    print('palette block')
    for head in (8, 12):
        s = make_palette(head=head)
        got = BK.palette_block(s)
        check('header %d discovered' % head, got == (head, 4, 256), repr(got))
        check('header %d: array closes on the section end' % head,
              got[0] + 2 * got[2] * got[1] == len(s))
        check('header %d: entry0 read back' % head,
              BK.entry0(s) == [0x03E0] * 4)

    # 0x03E0 is bits 5..9 set -> pure green, the colour-key value.
    check('0x03E0 decodes to #00FF00', BK.hex_rgb(0x03E0) == '#00FF00',
          BK.hex_rgb(0x03E0))
    check('0x0000 decodes to #000000', BK.hex_rgb(0x0000) == '#000000')
    check('0x7C00 decodes to blue', BK.hex_rgb(0x7C00) == '#0000FF',
          BK.hex_rgb(0x7C00))
    check('0x001F decodes to red', BK.hex_rgb(0x001F) == '#FF0000',
          BK.hex_rgb(0x001F))

    # THE REGRESSION THIS FILE EXISTS FOR. Section 9 opens with the literal
    # string "PALETTE" and is not a palette; the first version of the module
    # read it and produced "256x257 ends at 131608, BACK at 36" on all 711
    # fields. It has to be refused, not parsed.
    sec9 = (struct.pack('<HHB', 0, 1, 1) + b'PALETTE'
            + struct.pack('<I', 0) + struct.pack('<HHHH', 0, 0, 256, 1)
            + b'\x00' * 12 + b'BACK' + b'\x00' * 4096)
    try:
        BK.palette_block(sec9)
        check('section 9 is NOT parsed as a palette', False, 'it parsed')
    except BK.BgKeyError:
        check('section 9 is NOT parsed as a palette', True)

    # A section that does not close exactly must be refused, not guessed at.
    short = make_palette()[:-2]
    try:
        BK.palette_block(short)
        check('a truncated section is refused', False, 'it parsed')
    except BK.BgKeyError:
        check('a truncated section is refused', True)

    try:
        BK.palette_block(b'nothing like a palette')
        check('garbage refused', False, 'it parsed')
    except BK.BgKeyError:
        check('garbage refused', True)


def test_find_section():
    print('finding the section')
    parts = [b'\x00' * 40] * 9
    parts[BK.SECTION_PALETTE] = make_palette(pages=2)
    check('index 3 found', BK.find_palette_section(parts) == 3)

    # A reordered file must still be found rather than silently corrupted.
    moved = [b'\x00' * 40] * 9
    moved[6] = make_palette(pages=2)
    check('a moved palette is still found',
          BK.find_palette_section(moved) == 6)

    try:
        BK.find_palette_section([b'\x00' * 40] * 9)
        check('no palette anywhere is refused', False, 'it found one')
    except BK.BgKeyError:
        check('no palette anywhere is refused', True)


def test_blacken():
    print('blacken')
    s = make_palette(pages=3, entry0=0x03E0)
    out, n = BK.blacken(s, BK.MODE_BLACK)
    check('three pages changed', n == 3, str(n))
    check('LENGTH IS PRESERVED', len(out) == len(s),
          '%d vs %d' % (len(out), len(s)))
    check('all entry 0 now black', BK.entry0(out) == [0, 0, 0])

    diff = [i for i in range(len(s)) if s[i] != out[i]]
    head, _pages, cpp = BK.palette_block(s)
    want = sorted({head + 2 * cpp * p + b for p in range(3) for b in (0, 1)})
    check('exactly six bytes moved', diff == want,
          '%d bytes: %s' % (len(diff), diff[:8]))

    out1, n1 = BK.blacken(s, BK.MODE_FIRST)
    check('first mode changes one page', n1 == 1, str(n1))
    check('first mode leaves the rest',
          BK.entry0(out1) == [0, 0x03E0, 0x03E0])

    check('off mode is a no-op', BK.blacken(s, BK.MODE_OFF) == (s, 0))

    black = make_palette(entry0=0x0000)
    same, n0 = BK.blacken(black, BK.MODE_BLACK)
    check('already black -> 0 changes', n0 == 0)
    check('already black -> same object', same is black)

    twice, n2 = BK.blacken(out, BK.MODE_BLACK)
    check('idempotent', n2 == 0 and twice is out)


def test_mode():
    print('the setting')
    check('explicit empty is off', BK.mode('') == BK.MODE_OFF)
    check('"black"', BK.mode('black') == BK.MODE_BLACK)
    check('"first"', BK.mode('first') == BK.MODE_FIRST)
    check('"off"', BK.mode('off') == BK.MODE_OFF)
    check('"0"', BK.mode('0') == BK.MODE_OFF)
    check('"1" means black', BK.mode('1') == BK.MODE_BLACK)
    check('nonsense means off', BK.mode('purple') == BK.MODE_OFF)
    check('enabled() follows', BK.enabled('black') and not BK.enabled('off'))


def test_bucket():
    print('diag_bgkey colour buckets')
    try:
        import diag_bgkey as D
    except Exception as exc:                                   # noqa: BLE001
        check('diag_bgkey imports', False, str(exc)[:50])
        return
    check('diag_bgkey imports', True)
    check('#00FF00 is green', D.bucket(0x03E0) == 'green', D.bucket(0x03E0))
    check('#000000 is black', D.bucket(0x0000) == 'black')
    tan = (0xC8 * 31 // 255) | ((0xA8 * 31 // 255) << 5) \
        | ((0x78 * 31 // 255) << 10)
    check('a tan is tan', D.bucket(tan) == 'tan', D.bucket(tan))
    maroon = (0x88 * 31 // 255) | ((0x20 * 31 // 255) << 5) \
        | ((0x20 * 31 // 255) << 10)
    check('a maroon is maroon', D.bucket(maroon) == 'maroon',
          D.bucket(maroon))


# --------------------------------------------------------------------------
# the real archive
# --------------------------------------------------------------------------
def test_archive(path):
    print('flevel.lgp  %s' % path)
    import lgp
    arc = lgp.Archive(path)
    ok = black = 0
    sections = {}
    bad = []
    for name in arc.names():
        entry = arc.index.get(name)
        if entry is None or not arc.is_field(entry):
            continue
        try:
            parts = lgp.split_sections(arc.decompressed(entry))
            idx = BK.find_palette_section(parts)
            sections[idx] = sections.get(idx, 0) + 1
            pal = parts[idx]
            cols = BK.entry0(pal)
            ok += 1
            black += all(c == BK.BLACK for c in cols)
            out, n = BK.blacken(pal, BK.MODE_BLACK)
            if len(out) != len(pal):
                bad.append('%s: length changed' % name)
            if n and BK.entry0(out) != [0] * len(cols):
                bad.append('%s: not black after' % name)
        except Exception as exc:                               # noqa: BLE001
            bad.append('%s: %s' % (name, str(exc)[:60]))
    check('fields parsed', ok > 600, '%d parsed' % ok)
    check('no length ever changes', not bad, '; '.join(bad[:3]))
    check('the palette is always the same section', len(sections) == 1,
          repr(sections))
    print('    %d field(s), %d already fully black, %d would be rewritten'
          % (ok, black, ok - black))
    if ok and black == ok:
        print('    NOTE: every field is already black. Either this archive')
        print('    was already normalised, or the margin colours come from')
        print('    somewhere else -- run diag_bgkey.py --predict before')
        print('    spending a build.')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--flevel', default=os.environ.get(
        'SEVENTH_NX_TEST_FLEVEL', ''))
    a = ap.parse_args()

    test_parse()
    test_find_section()
    test_blacken()
    test_mode()
    test_bucket()
    if a.flevel and os.path.exists(a.flevel):
        test_archive(a.flevel)
    else:
        print('flevel.lgp  SKIPPED -- pass --flevel or set '
              'SEVENTH_NX_TEST_FLEVEL')

    print()
    if FAILED:
        print('%d FAILED: %s' % (len(FAILED), ', '.join(FAILED)))
        return 1
    print('all ok')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
