#!/usr/bin/env python3
"""
test_battlecap.py -- the battle size ceiling and the TEX header repair.

Both passes are checked against REAL textures pulled out of the built
archives, not synthetic ones, because the whole point of both is that the
real files disagreed with what the build said about them.

    python3 test_battlecap.py
    python3 test_battlecap.py --battle <battle.lgp> --char <char.lgp>

WHAT EACH GROUP IS FOR
----------------------
1. ceiling    -- resolution from the environment. An unparseable value must
                 fall back to the background cap, never silently to "off":
                 a ceiling nobody can read is not permission to ship 1024px.
2. cap        -- every oversized texture comes under the ceiling with its
                 FORMAT BYTE-IDENTICAL. That is the safety argument for
                 applying it to player skins, whose exemption from the
                 converter exists because they are proven pixel-perfect.
3. headers    -- only fields that contradict the payload are touched, one
                 word each, pixels untouched, healthy files passed through.
4. idempotent -- both passes are re-runnable over their own output.
5. wiring     -- the build calls both, at both battle sites, after the
                 converter.
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

FAILURES: list[str] = []
SKIPPED: list[str] = []


def check(label, ok, detail=''):
    print('  %-58s %s%s' % (label, 'ok' if ok else 'FAIL',
                            ('  -- ' + detail) if detail and not ok else ''))
    if not ok:
        FAILURES.append(label)
    return ok


def skip(label, why):
    print('  %-58s skipped  -- %s' % (label, why))
    SKIPPED.append(label)


TITLE = '0100A5B00BDC6000'
SD = HERE / 'sdout' / 'atmosphere' / 'contents' / TITLE / 'romfs' / 'ff7' \
    / 'workingdir' / 'data'


def test_ceiling():
    import ff7nx_battlecap as B
    print('\nceiling')
    E = B.CEILING_ENV
    check('unset follows the background cap', B.ceiling(768, {}) == 768)
    check('unset follows a 256 background cap', B.ceiling(256, {}) == 256)
    check('explicit value wins', B.ceiling(768, {E: '512'}) == 512)
    check('explicit 0 disables the pass', B.ceiling(768, {E: '0'}) is None)
    check('negative disables the pass', B.ceiling(768, {E: '-4'}) is None)
    # The important one. An unreadable ceiling must NOT become "no ceiling".
    check('garbage falls back to the cap, NOT to off',
          B.ceiling(768, {E: 'huge'}) == 768)
    check('no background cap and nothing set -> off',
          B.ceiling(None, {}) is None)


def _pull(archive, pred, limit=None):
    """[(name, payload, parsed)] for entries matching pred."""
    import lgp
    import tex
    out = []
    for e in lgp.Archive(str(archive)).entries:
        t = tex.parse(e['payload'])
        if t is not None and pred(e['name'].lower(), t):
            out.append((e['name'], e['payload'], t))
            if limit and len(out) >= limit:
                break
    return out


def test_cap(battle):
    import build
    import tex
    print('\ncap, against real oversized textures')
    over = _pull(battle, lambda n, t: max(t['width'], t['height']) > 768)
    if not over:
        skip('cap', 'no texture over 768px in %s' % battle)
        return
    print('  %d texture(s) over 768px in the archive' % len(over))
    td = tempfile.mkdtemp()
    mod = {}
    for n, d, _t in over:
        p = os.path.join(td, n)
        open(p, 'wb').write(d)
        mod[n.lower()] = (p, None)
    out = build._cap_battle_textures('battle.lgp', mod, lambda *_: None, 768)

    fmt_ok = fits = aspect = True
    for n, d, t in over:
        nt = tex.parse(open(out[n.lower()][0], 'rb').read())
        fmt_ok = fmt_ok and (
            nt['palette_flag'] == t['palette_flag']
            and nt['bytes_per_pixel'] == t['bytes_per_pixel']
            and nt['palette_size'] == t['palette_size']
            and nt['num_palettes'] == t['num_palettes']
            and nt['colors_per_palette'] == t['colors_per_palette']
            and nt['palette'] == t['palette'])
        fits = fits and max(nt['width'], nt['height']) <= 768
        aspect = aspect and abs(t['width'] / t['height']
                                - nt['width'] / nt['height']) < 0.02
    check('every oversized texture is now within the ceiling', fits)
    check('format is byte-identical (palette, depth, entry count)', fmt_ok)
    check('aspect ratio preserved', aspect)

    # A texture already inside the ceiling must be returned untouched --
    # otherwise the pass would rewrite most of the archive for nothing.
    under = _pull(battle, lambda n, t: max(t['width'], t['height']) <= 256,
                  limit=8)
    td2 = tempfile.mkdtemp()
    mod2 = {}
    for n, d, _t in under:
        p = os.path.join(td2, n)
        open(p, 'wb').write(d)
        mod2[n.lower()] = (p, None)
    out2 = build._cap_battle_textures('battle.lgp', mod2, lambda *_: None, 768)
    check('textures already within the ceiling pass through untouched',
          all(out2[n.lower()][0] == mod2[n.lower()][0] for n, _d, _t in under))

    # idempotent
    again = build._cap_battle_textures(
        'battle.lgp', {k: (v[0], None) for k, v in out.items()},
        lambda *_: None, 768)
    same = all(open(again[k][0], 'rb').read() == open(v[0], 'rb').read()
               for k, v in out.items())
    check('re-running the cap changes nothing further', same)

    # a LOWER ceiling must still bite on the already-capped output
    lower = build._cap_battle_textures(
        'battle.lgp', {k: (v[0], None) for k, v in out.items()},
        lambda *_: None, 256)
    ok = all(max(tex.parse(open(lower[k][0], 'rb').read())['width'],
                 tex.parse(open(lower[k][0], 'rb').read())['height']) <= 256
             for k in out)
    check('a lower ceiling still applies to already-capped output', ok)


def test_headers(char):
    import build
    import tex
    print('\nTEX header repair')
    bad = _pull(char, lambda n, t: (
        struct.unpack_from('<I', b'', 0) if False else True))
    # find the genuinely inconsistent ones plus a healthy control
    import lgp
    broken, healthy = [], []
    for e in lgp.Archive(str(char)).entries:
        t = tex.parse(e['payload'])
        if t is None:
            continue
        bpp = struct.unpack_from('<I', e['payload'], tex.O_BITS_PER_PIXEL)[0]
        if bpp != t['bytes_per_pixel'] * 8:
            broken.append((e['name'], e['payload'], t))
        elif len(healthy) < 3:
            healthy.append((e['name'], e['payload'], t))
    print('  %d self-inconsistent, %d healthy control(s)'
          % (len(broken), len(healthy)))
    if not broken:
        skip('header repair', 'no inconsistent TEX in %s' % char)
        return
    td = tempfile.mkdtemp()
    mod = {}
    for n, d, _t in broken + healthy:
        p = os.path.join(td, n)
        open(p, 'wb').write(d)
        mod[n.lower()] = (p, None)
    out = build._normalise_tex_headers('char.lgp', mod, lambda *_: None)

    one_word = pixels_ok = True
    for n, d, t in broken:
        nd = open(out[n.lower()][0], 'rb').read()
        diff = [i for i in range(len(d)) if d[i] != nd[i]]
        one_word = one_word and diff == list(range(tex.O_BITS_PER_PIXEL,
                                                   tex.O_BITS_PER_PIXEL + 1))
        nt = tex.parse(nd)
        pixels_ok = pixels_ok and nt['pixels'] == t['pixels'] \
            and nt['palette'] == t['palette']
        one_word = one_word and struct.unpack_from(
            '<I', nd, tex.O_BITS_PER_PIXEL)[0] == t['bytes_per_pixel'] * 8
    check('exactly one byte changes, at the bits_per_pixel field', one_word)
    check('pixels and palette are untouched', pixels_ok)
    check('healthy files are passed through unmodified',
          all(out[n.lower()][0] == mod[n.lower()][0] for n, _d, _t in healthy))
    again = build._normalise_tex_headers(
        'char.lgp', {k: (v[0], None) for k, v in out.items()},
        lambda *_: None)
    check('re-running the repair changes nothing further',
          all(again[k][0] == v[0] for k, v in out.items()))


def test_wiring():
    print('\nbuild wiring')
    src = (HERE / 'build.py').read_text(encoding='utf-8')
    check('_cap_battle_textures exists', 'def _cap_battle_textures(' in src)
    check('_normalise_tex_headers exists',
          'def _normalise_tex_headers(' in src)
    check('the ceiling pass runs at BOTH battle sites',
          src.count('_cap_battle_textures(name, mod_files, log, _ceil)') == 2)
    check('it runs AFTER the converter',
          all(src.index('_convert_battle_textures(\n', 0, m) < m
              for m in [src.index('_ceil = ff7nx_battlecap.ceiling')]))
    check('the header repair runs on the model archives',
          '_normalise_tex_headers(name, mod_files, log)' in src)
    check('the ceiling follows the background cap',
          'ff7nx_battlecap.ceiling(_battle_bg_tex_cap())' in src)


def main_(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--battle', default=str(SD / 'battle' / 'battle.lgp'))
    ap.add_argument('--char', default=str(SD / 'field' / 'char.lgp'))
    a = ap.parse_args(argv)

    print('== battle ceiling + TEX header repair')
    test_ceiling()
    test_wiring()
    for label, path, fn in (('battle', a.battle, test_cap),
                            ('char', a.char, test_headers)):
        if not Path(path).is_file():
            skip(label, 'no archive at %s' % path)
            continue
        fn(Path(path))

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
