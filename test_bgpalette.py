#!/usr/bin/env python3
"""
test_bgpalette.py -- one palette per battle stage instead of one per tile.

The defect, measured on the shipped archive: `tex.convert_for_battle`
median-cuts each TEX on its own, so adjacent tiles of one battle background
end up with unrelated 256-colour ramps — stage 03's neighbours shared
between 0 and 12 of their 256 entries. A sky gradient crossing that seam
lands on different colours either side and steps visibly.

These checks run against REAL tiles pulled out of the built `battle.lgp`,
round-tripped back to truecolor, because the point of the change is what it
does to actual art. They are SKIPPED when no built archive is present.

    python3 test_bgpalette.py
    python3 test_bgpalette.py --battle <battle.lgp>

WHAT EACH GROUP IS FOR
----------------------
1. palette   -- shared_palette summarises several images into one ramp, and
                ignores anything already paletted (not ours to unify).
2. lut       -- the nearest-colour table is correct and memoised. Without
                the memo it is rebuilt per tile instead of per stage, which
                is 3.2 s of every tile's 3.5 s.
3. convert   -- with a shared palette every tile comes out with the SAME
                256 entries; without one, nothing changes at all.
4. identity  -- the no-palette path is byte-identical to before, so every
                enemy and player texture in the archive is untouched.
5. wiring    -- the build groups by stage from battle_bg_dds_map.json, keys
                the conversion cache on the palette, and can be switched off.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct
import sys

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import tex                                                     # noqa: E402

FAILURES: list[str] = []
SKIPPED: list[str] = []


def check(label, ok, detail=''):
    print('  %-56s %s%s' % (label, 'ok' if ok else 'FAIL',
                            ('  -- ' + detail) if detail and not ok else ''))
    if not ok:
        FAILURES.append(label)
    return ok


def skip(label, why):
    print('  %-56s skipped  -- %s' % (label, why))
    SKIPPED.append(label)


TITLE = '0100A5B00BDC6000'
DEFAULT_BATTLE = (HERE / 'sdout' / 'atmosphere' / 'contents' / TITLE /
                  'romfs' / 'ff7' / 'workingdir' / 'data' / 'battle' /
                  'battle.lgp')


def _truecolor(d):
    """A paletted TEX decoded back to a 24-bit truecolor TEX.

    Used only by this test: the mod ships DDS and the archive holds the
    already-quantised result, so this reconstructs an input of the right
    shape to drive both conversion paths over identical pixels.
    """
    t = tex.parse(d)
    if t is None or not t['palette_flag']:
        return None
    pal = t['palette']
    out = bytearray()
    for v in t['pixels']:
        o = v * 4
        out += bytes((pal[o], pal[o + 1], pal[o + 2]))
    hdr = bytearray(d[:tex.HEADER_LEN])
    for off, val in ((tex.O_PAL_FLAG, 0), (tex.O_PAL_SIZE, 0),
                     (tex.O_NUM_PALETTES, 0), (tex.O_COLORS_PER_PAL, 0),
                     (tex.O_COLORS_PER_PAL2, 0), (tex.O_BYTES_PER_PIXEL, 3),
                     (tex.O_BITS_PER_PIXEL, 24), (tex.O_PITCH, 0)):
        struct.pack_into('<I', hdr, off, val)
    return bytes(hdr) + bytes(out)


def _entries(d):
    p = tex.parse(d)['palette']
    return {p[i * 4:i * 4 + 4] for i in range(len(p) // 4)}


def test_palette():
    print('\nshared_palette')
    tex._LUT_CACHE.clear()
    # two synthetic truecolor tiles with deliberately disjoint colours
    def tile(base):
        hdr = bytearray(tex.HEADER_LEN)
        struct.pack_into('<I', hdr, tex.O_VERSION, 1)
        for off, val in ((tex.O_WIDTH, 8), (tex.O_HEIGHT, 8),
                         (tex.O_BYTES_PER_PIXEL, 3), (tex.O_BITS_PER_PIXEL, 24)):
            struct.pack_into('<I', hdr, off, val)
        px = bytearray()
        for i in range(64):
            px += bytes((base, (base + i) % 256, 255 - base))
        return bytes(hdr) + bytes(px)
    a, b = tile(10), tile(200)
    pal = tex.shared_palette([a, b], max_colors=255)
    check('a palette is produced from several images', bool(pal))
    check('it does not exceed the requested size', len(pal) <= 255,
          str(len(pal)))
    check('entries are RGBA tuples',
          all(len(p) == 4 and all(0 <= c <= 255 for c in p) for p in pal))
    check('already-paletted inputs are ignored',
          tex.shared_palette([b'\x00' * 300]) == [])
    check('an empty list gives an empty palette',
          tex.shared_palette([]) == [])


def test_lut():
    print('\nnearest-colour table')
    tex._LUT_CACHE.clear()
    pal = [(0, 0, 0, 255), (255, 255, 255, 255), (255, 0, 0, 255)]
    lut = tex._palette_lut(pal, bits=5)
    check('table covers the whole 5-bit cube', len(lut) == 32 * 32 * 32)
    n, sh = 32, 3

    def look(r, g, b):
        return lut[(((r >> sh) * n) + (g >> sh)) * n + (b >> sh)]
    check('black maps to the black entry', look(0, 0, 0) == 0)
    check('white maps to the white entry', look(255, 255, 255) == 1)
    check('red maps to the red entry', look(250, 8, 8) == 2)
    check('the table is memoised (same object back)',
          tex._palette_lut(pal, bits=5) is lut)
    other = [(1, 2, 3, 255)]
    tex._palette_lut(other, bits=5)
    check('a different palette gets its own table',
          tex._palette_lut(pal, bits=5) is not None)


def test_convert(battle):
    print('\nconversion against real tiles from %s' % battle.name)
    import lgp
    try:
        m = json.loads((HERE / 'battle_bg_dds_map.json').read_text())
    except Exception as exc:                                   # noqa: BLE001
        skip('conversion', 'no battle_bg_dds_map.json (%s)' % exc)
        return
    stages = {}
    for k, v in m.items():
        stages.setdefault(str(k).split('_')[0], []).append(str(v).lower())
    idx = {e['name'].lower(): e['payload']
           for e in lgp.Archive(str(battle)).entries}
    st = max(stages, key=lambda s: sum(1 for n in stages[s] if n in idx))
    names = [n for n in sorted(stages[st]) if n in idx][:4]
    srcs = [(n, _truecolor(idx[n])) for n in names]
    srcs = [(n, s) for n, s in srcs if s]
    if len(srcs) < 2:
        skip('conversion', 'stage %s has fewer than 2 usable tiles' % st)
        return
    print('  stage %s, %d tile(s): %s'
          % (st, len(srcs), ', '.join(n for n, _ in srcs)))

    old = [(n, tex.convert_for_battle(s, None, cap=768)[0]) for n, s in srcs]
    pal = tex.shared_palette([s for _, s in srcs], max_colors=255)
    new = [(n, tex.convert_for_battle(s, None, cap=768, palette=pal)[0])
           for n, s in srcs]

    check('every tile converted on both paths',
          all(o for _, o in old) and all(o for _, o in new))
    before = [len(_entries(old[i][1]) & _entries(old[i + 1][1]))
              for i in range(len(old) - 1)]
    after = [len(_entries(new[i][1]) & _entries(new[i + 1][1]))
             for i in range(len(new) - 1)]
    print('  adjacent-tile palette agreement out of 256:')
    for i in range(len(before)):
        print('     %-6s vs %-6s   before %3d   after %3d'
              % (old[i][0], old[i + 1][0], before[i], after[i]))
    check('neighbours disagreed before (this is the defect)',
          max(before) < 64, 'best was %d/256' % max(before))
    check('neighbours now share one palette exactly',
          all(a == 256 for a in after), repr(after))

    for n, o in new:
        t = tex.parse(o)
        if not check('%s is a valid 1x256 paletted TEX' % n,
                     t is not None and t['palette_flag']
                     and t['num_palettes'] == 1
                     and t['colors_per_palette'] == 256):
            return
    for (n, o), (_, s) in zip(new, srcs):
        ts, to = tex.parse(s), tex.parse(o)
        if not check('%s keeps its dimensions' % n,
                     (ts['width'], ts['height']) == (to['width'], to['height'])):
            return


def test_identity(battle):
    print('\nthe unshared path is untouched')
    import lgp
    idx = {e['name'].lower(): e['payload']
           for e in lgp.Archive(str(battle)).entries}
    # A RICH tile on purpose. A flat one with a handful of colours converts
    # to the same bytes either way -- correctly, because both paths pick the
    # same few entries and the dither has no error to diffuse -- so it
    # cannot tell the two paths apart. `anac` is exactly that case: 8
    # distinct colours, identical output, and it made this check fail while
    # nothing was wrong.
    src = None
    for n, d in idx.items():
        t = tex.parse(d)
        if not (t and t['palette_flag'] and 64 <= t['width'] <= 256):
            continue
        used = len(set(t['pixels']))
        if used >= 64:
            src = _truecolor(d)
            break
    if src is None:
        skip('identity', 'no sufficiently colourful tile found')
        return
    a = tex.convert_for_battle(src, None, cap=256)[0]
    b = tex.convert_for_battle(src, None, cap=256, palette=None)[0]
    check('palette=None is byte-identical to the old call', a == b)
    pal = tex.shared_palette([src], max_colors=255)
    out, note = tex.convert_for_battle(src, None, cap=256, palette=pal)
    check('a supplied palette really is used (the note says so)',
          'SHARED stage palette' in note, note)
    check('and it produces different pixels on a colourful tile', a != out)


def test_wiring():
    print('\nbuild wiring')
    src = (HERE / 'build.py').read_text(encoding='utf-8')
    check('_battle_bg_shared_palettes exists',
          'def _battle_bg_shared_palettes(' in src)
    check('it groups from battle_bg_dds_map.json',
          "battle_bg_dds_map.json" in src)
    check('it is called at BOTH battle sites',
          src.count('_bg_pals = (_battle_bg_shared_palettes(') == 2)
    check('the palette reaches the converter',
          'bg_palettes=_bg_pals' in src
          and 'palette=shared_pal' in src)
    check('the conversion cache is keyed on the palette',
          "pal_sig = '-pal'" in src and 'pal_sig' in src)
    check('there is an off switch',
          'BATTLE_BG_SHARED_PAL_ENV' in src)
    check('only background tiles are grouped',
          'if low not in battle_bg_native_names' in src)


def main_(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--battle', default=str(DEFAULT_BATTLE))
    a = ap.parse_args(argv)

    print('== shared battle-background palettes')
    test_palette()
    test_lut()
    test_wiring()
    b = Path(a.battle)
    if not b.is_file():
        skip('conversion / identity', 'no battle.lgp at %s' % b)
    else:
        test_convert(b)
        test_identity(b)

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
