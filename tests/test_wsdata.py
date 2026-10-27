#!/usr/bin/env python3
"""
test_wsdata.py -- the widescreen field data, checked against its sources.

WHAT IS BEING PROTECTED
=======================
Two things in `ff7nx_wsdata.py` are the kind that fail silently:

1. **A hand-written LZSS decompressor.** It exists because PyFF7's correct
   one decompresses the whole field -- section 9, the background, is most of
   it -- and reading 711 fields that way had not finished in three minutes.
   Stopping at section 8 makes it two seconds. A decompressor that is subtly
   wrong does not crash; it returns plausible little numbers, and every
   coverage figure built on it is quietly wrong. So it is checked field by
   field against PyFF7's own reader on the real archive.

2. **The gate.** `427` is not a number anyone should be able to change
   without noticing: it is `game_width/2 + |wide_viewport_x|`, and both of
   those come from FFNx. It is asserted from its parts, not written down.

The field-data tests skip cleanly when no `flevel.lgp` is to hand, so this
runs in CI; the parser tests always run.

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
import random
import struct
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.dirname(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ff7nx_wsdata as W                                     # noqa: E402

FAILURES = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILURES.append(msg)


def find_flevel():
    for rel in (('dump', 'romfs', 'ff7', 'resources', 'ff7_1.02', 'data',
                 'field', 'flevel.lgp'),
                ('flevel.lgp',), ('..', 'flevel.lgp')):
        p = os.path.join(os.path.dirname(_HERE), *rel)
        if os.path.exists(p):
            return p
    p = os.environ.get('SEVENTH_NX_FLEVEL', '')
    if p and os.path.exists(p):
        return p
    # game_data_zips/field.zip
    z = os.path.join(os.path.dirname(_HERE), 'game_data_zips', 'field.zip')
    if os.path.exists(z):
        import zipfile
        zf = zipfile.ZipFile(z)
        for n in zf.namelist():
            if os.path.basename(n).lower() == 'flevel.lgp':
                tmp = os.path.join(tempfile.mkdtemp(prefix='wstest'),
                                   'flevel.lgp')
                with open(tmp, 'wb') as f:
                    f.write(zf.read(n))
                return tmp
    return None


# --------------------------------------------------------------------- 1
def test_gate_is_derived():
    print('\n1. the gate is its parts, not a magic number')
    check(W.GAME_W == 640 and W.GAME_H == 480,
          'game_width/height are 640x480 and are never patched')
    check(W.WIDE_VIEWPORT_WIDTH == 854 and W.WIDE_VIEWPORT_X == -107,
          'FFNx viewport is %d wide at x=%d'
          % (W.WIDE_VIEWPORT_WIDTH, W.WIDE_VIEWPORT_X))
    # 640*4/3 is 853.33; FFNx rounds UP, and the odd byte matters because
    # wide_viewport_x is derived from it by integer halving.
    import math
    check(W.WIDE_VIEWPORT_WIDTH == math.ceil(W.GAME_W * 4 / 3.0),
          '854 = ceil(640 * 4/3) = ceil(%.2f)' % (W.GAME_W * 4 / 3.0))
    check(W.WIDE_VIEWPORT_WIDTH / float(W.GAME_H) > 1.777,
          'and %d/%d = %.4f really is 16:9'
          % (W.WIDE_VIEWPORT_WIDTH, W.GAME_H,
             W.WIDE_VIEWPORT_WIDTH / float(W.GAME_H)))
    check(W.WIDE_VIEWPORT_X == -(W.WIDE_VIEWPORT_WIDTH - W.GAME_W) // 2,
          '-107 = -(854-640)/2')
    check(W.WIDE_GATE == W.GAME_W // 2 + abs(W.WIDE_VIEWPORT_X) == 427,
          'gate = %d' % W.WIDE_GATE)
    check(W.gate(427) and not W.gate(426),
          'the boundary is >=, not >')


# --------------------------------------------------------------------- 2
def test_lzss_against_pyff7():
    print('\n2. the bounded decompressor agrees with PyFF7, field by field')
    flevel = find_flevel()
    if not flevel:
        print('  skip  no flevel.lgp (set SEVENTH_NX_FLEVEL)')
        return
    try:
        pyff7 = os.path.join(os.path.dirname(_HERE), 'PyFF7')
        if os.path.isdir(pyff7) and pyff7 not in sys.path:
            sys.path.insert(0, pyff7)
        from PyFF7.lgp import LGP
        from PyFF7.lzss import decompress_lzss
        from PyFF7.field import Triggers
    except ImportError as exc:
        print('  skip  PyFF7 not importable: %s' % exc)
        return

    mine = W.camera_ranges(flevel)
    check(len(mine) > 600, 'read %d fields' % len(mine))

    lgp = LGP(flevel)
    entries = [e for e in lgp if e['filename'] in mine]
    random.seed(3)
    sample = random.sample(entries, min(40, len(entries)))
    # PyFF7 labels the four s16 `left, bottom, right, top`. FFNx's struct
    # -- which is the one the game's own code indexes -- says
    # `left, top, right, bottom` (src/ff7.h:2370), and Y grows downward.
    # So the BYTES must agree positionally while the LABELS are crossed on
    # the vertical pair. Both halves are asserted: the first proves the
    # decompressor is right, the second pins the discrepancy so nobody
    # "fixes" our order back to PyFF7's.
    bad = []
    crossed = 0
    for e in sample:
        data = decompress_lzss(lgp.load_toc_entry(e))
        starts = struct.unpack('<9I', data[6:42])
        body = data[starts[7] + 4:starts[8]]
        raw = struct.unpack('<4h', body[12:20])          # positional truth
        got = mine[e['filename']]
        ours = tuple(got[k] for k in W.RANGE_ORDER)
        if raw != ours:
            bad.append((e['filename'], raw, ours))
        ref = Triggers(body).camera_range
        if (ref['bottom'], ref['top']) == (got['top'], got['bottom']):
            crossed += 1
    check(not bad,
          '%d random fields match the raw bytes positionally' % len(sample)
          if not bad else '%d mismatches, first: %s' % (len(bad), bad[0]))
    check(crossed == len(sample),
          'and PyFF7 labels the vertical pair the other way round on all '
          '%d, exactly as expected' % len(sample))

    # The order left,bottom,right,top is the thing most likely to be got
    # wrong, and a swapped pair is invisible in `width`. Check `height`.
    sane = sum(1 for v in mine.values()
               if v['width'] > 0 and v['height'] > 0)
    check(sane == len(mine),
          'every field has a positive width AND height (%d/%d)'
          % (sane, len(mine)))
    down = sum(1 for v in mine.values() if v['bottom'] > v['top'])
    check(down == len(mine),
          'and bottom > top on all %d, confirming Y grows downward'
          % len(mine))


# --------------------------------------------------------------------- 3
def test_resolve():
    print('\n3. per-field resolution matches FFNx\'s own logic')
    ranges = {
        'wide':   {'left': -256, 'right': 256, 'top': -256, 'bottom': 256,
                   'width': 512, 'height': 512},
        'narrow': {'left': -160, 'right': 160, 'top': -160, 'bottom': 160,
                   'width': 320, 'height': 320},
        'near':   {'left': -200, 'right': 200, 'top': -160, 'bottom': 160,
                   'width': 400, 'height': 320},
    }
    r = W.resolve(ranges)
    check(r['wide']['mode'] == W.WM_EXTEND_WIDE, 'width 512 -> extend_wide')
    check(r['narrow']['mode'] == W.WM_DISABLED, 'width 320 -> disabled')
    check(r['near']['mode'] == W.WM_DISABLED,
          'width 400 -> disabled (a near miss, not a pass)')

    # a config that widens a near-miss field re-runs the gate
    r = W.resolve(ranges, {'near': {'left': -240, 'right': 240}})
    check(r['near']['mode'] == W.WM_EXTEND_WIDE,
          'config widening 400 -> 480 turns the field on')
    check(r['near']['overrides'] == ['left', 'right'],
          'and records which keys did it')
    check(not r['near']['gated_in'],
          'while remembering the gate alone would have refused it')

    # an explicit mode wins over the gate, both ways
    r = W.resolve(ranges, {'wide': {'mode': W.WM_DISABLED}})
    check(r['wide']['mode'] == W.WM_DISABLED,
          'an explicit mode=0 turns a gated-in field off')

    r = W.resolve(ranges, {'wide': {'mode': W.WM_ZOOM}})
    check(r['wide']['mode'] == W.WM_ZOOM, 'mode=2 -> zoom')
    # zoom crops vertically: 9*512/16 - 240 = 48, so 24 off each edge
    check(r['wide']['range']['bottom'] == 256 - 24
          and r['wide']['range']['top'] == -256 + 24,
          'and zoom CROPS the vertical range by 9*w/16-240, halved each '
          'side (512 -> 464)')


# --------------------------------------------------------------------- 4
def test_toml_subset():
    print('\n4. the config parser, on the shapes FFNx actually writes')
    text = '''
# a comment
["md1_1"]
left = -320
right = 320
mode = 3
reset_vertical_pos = true
scripted_clip = false

[nmkin_1]
h_offset = -16
v_offset = 8
'''
    got = W._parse_toml_subset(text)
    check(set(got) == {'md1_1', 'nmkin_1'},
          'two tables, quoted and unquoted names')
    check(got['md1_1']['left'] == -320 and got['md1_1']['mode'] == 3,
          'negative and positive ints')
    check(got['md1_1']['reset_vertical_pos'] is True
          and got['md1_1']['scripted_clip'] is False, 'bools')
    check(got['nmkin_1']['h_offset'] == -16, 'second table is separate')

    mov = W._parse_toml_subset(
        '[opening]\nmode = 4\n'
        'movie_v_offset = [ [0, 0], [120, -12], [300, 24] ]\n')
    check(mov['opening']['mode'] == 4, 'movie mode')
    check(mov['opening']['movie_v_offset'] == [[0, 0], [120, -12], [300, 24]],
          'movie_v_offset is an array of [frame, v_offset] pairs '
          '(src/ff7/widescreen.cpp:444 reads get(0)/get(1))')

    # and it agrees with the real thing when the real thing is available
    try:
        import tomllib
        ref = tomllib.loads(text)
        check(ref['md1_1'] == got['md1_1'],
              'identical to tomllib on the same input')
    except ImportError:
        print('  skip  tomllib not available (Python < 3.11)')


# --------------------------------------------------------------------- 5
def test_unknown_keys():
    print('\n5. keys we do not consume are reported, not dropped silently')
    cfg = {'a': {'left': 1, 'mystery': 2}, 'b': {'right': 3}}
    check(W.unknown_keys(cfg) == ['mystery'], 'mystery is flagged')
    check(W.unknown_keys({'a': {k: 1 for k in W.FIELD_KEYS}}) == [],
          'the documented key set is fully consumed')


def test_section8_bake():
    print('\n6. the section 8 camera-range bake')
    # a synthetic section 8: 9-byte name, s8 dir, s16 focus, then the range
    # left, TOP, right, BOTTOM -- FFNx's struct order, Y downward
    sec8 = (b'md1_1\0\0\0\0' + bytes([0]) + b'\x00\x00'
            + struct.pack('<4h', -160, -120, 160, 120) + b'\xAA' * 64)
    got = W.read_section8_range(sec8)
    check(got['left'] == -160 and got['right'] == 160
          and got['top'] == -120 and got['bottom'] == 120,
          'reads left/top/right/bottom in that order')
    check(got['width'] == 320 and got['height'] == 240,
          'and derives w/h with height = bottom - top')

    out = W.write_section8_range(sec8, {'left': -240, 'top': -120,
                                        'right': 240, 'bottom': 120})
    check(len(out) == len(sec8),
          'the section length does not change, so nothing else moves')
    check(out[:W.RANGE_OFFSET] == sec8[:W.RANGE_OFFSET]
          and out[W.RANGE_OFFSET + 8:] == sec8[W.RANGE_OFFSET + 8:],
          'and only those eight bytes differ')
    check(W.read_section8_range(out)['width'] == 480, 'round trips')

    # the refusals. Each of these would put the camera outside the painted
    # background, and the symptom would be a hardware test.
    for bad, why in (
            ({'left': -40000, 'top': -120, 'right': 240, 'bottom': 120},
             'a value outside s16'),
            ({'left': 240, 'top': -120, 'right': -240, 'bottom': 120},
             'an inverted horizontal range'),
            ({'left': -240, 'top': 120, 'right': 240, 'bottom': -120},
             'an inverted vertical range -- this is the one that caught '
             'the crossed labels on the real config')):
        try:
            W.write_section8_range(sec8, bad)
            check(False, 'refuses %s' % why)
        except ValueError:
            check(True, 'refuses %s' % why)

    check_short = True
    try:
        W.write_section8_range(b'\x00' * 4, {'left': 0, 'top': 0,
                                              'right': 1, 'bottom': 1})
        check_short = False
    except ValueError:
        pass
    check(check_short, 'refuses a section 8 too short to hold a range')


def test_bake_plan_and_verify():
    print('\n7. the plan only contains fields that actually change')
    ranges = {
        'a': {'left': -160, 'right': 160, 'top': -120, 'bottom': 120,
              'width': 320, 'height': 240},
        'b': {'left': -256, 'right': 256, 'top': -256, 'bottom': 256,
              'width': 512, 'height': 512},
    }
    plan = W.bake_plan(ranges, {'a': {'left': -240, 'right': 240}})
    check(set(plan) == {'a'}, 'only the changed field is in the plan')
    check(plan['a']['right'] == 240, 'with the new value')

    plan2 = W.bake_plan(ranges, {'b': {'mode': W.WM_EXTEND_WIDE}})
    check(plan2 == {}, 'a mode-only entry changes no range, so no re-encode')

    # verify_bake is what makes the bake falsifiable without a console
    after = {'a': dict(ranges['a'], left=-240, right=240, width=480),
             'b': ranges['b']}
    ok, problems = W.verify_bake(ranges, after, plan)
    check(ok, 'a correct rebuild verifies clean')

    ok, problems = W.verify_bake(ranges, {'a': ranges['a'], 'b': ranges['b']},
                                 plan)
    check(not ok and 'left' in problems[0],
          'a change that did not land is caught')

    drifted = {'a': after['a'], 'b': dict(ranges['b'], right=999)}
    ok, problems = W.verify_bake(ranges, drifted, plan)
    check(not ok and 'not in the plan' in problems[0],
          'a field that moved without being asked to is caught')

    ok, problems = W.verify_bake(ranges, {'a': after['a']}, plan)
    check(not ok and 'lost' in problems[0], 'a lost field is caught')


def test_exception_table():
    print('\n8. the per-field table the module will need')
    ranges = {
        'wide1': {'left': -256, 'right': 256, 'top': -256, 'bottom': 256,
                  'width': 512, 'height': 512},
        'wide2': {'left': -256, 'right': 256, 'top': -256, 'bottom': 256,
                  'width': 512, 'height': 512},
        'narrow': {'left': -160, 'right': 160, 'top': -160, 'bottom': 160,
                   'width': 320, 'height': 320},
    }
    res = W.resolve(ranges)
    wd, exc, zoom = W.exception_list(res)
    check(wd is True, '2 wide of 3 -> default WIDE')
    check(exc == ['narrow'], 'and the minority is named')

    blob, text, info = W.emit_exception_table(res, 'unit test')
    check(info['listed'] == 1 and info['wide_default'],
          'the table names the shorter side (%d entries)' % info['listed'])
    check(len(blob) == W.NAME_LEN, 'blob is one fixed-width record')
    check(blob == b'narrow\0\0\0', 'NUL padded to %d, as section 8 stores '
                                     'it' % W.NAME_LEN)

    # flip the majority and the table must flip with it
    res2 = W.resolve({k: v for k, v in ranges.items() if k != 'wide2'})
    _b, _t, info2 = W.emit_exception_table(res2, 'unit test')
    check(info2['listed'] == 1, 'a 1-1 split still names one side')

    # archive-name variants collapse onto the section 8 name
    check(W.field_key('crcin_1.XOne') == 'crcin_1',
          'field_key strips the archive variant suffix')
    check(W.field_key('MD1_1') == 'md1_1', 'and lowercases')
    var = dict(ranges)
    var['narrow.XOne'] = dict(ranges['narrow'])
    _b, _t, info3 = W.emit_exception_table(W.resolve(var), 'unit test')
    check(info3['listed'] == 1,
          'a field and its .XOne twin are one entry, not two')
    check(info3['ambiguous'] == [],
          'and are not flagged ambiguous when they agree')

    # A name that will not fit is refused rather than silently truncated.
    # It has to be on the LISTED side to matter, so make it the minority.
    long_ranges = {'a_very_long_field_name': ranges['wide1'],
                   'n1': ranges['narrow'], 'n2': ranges['narrow'],
                   'n3': ranges['narrow']}
    try:
        W.emit_exception_table(W.resolve(long_ranges), 'unit test')
        check(False, 'refuses a name longer than %d bytes' % W.NAME_LEN)
    except ValueError:
        check(True, 'refuses a name longer than %d bytes' % W.NAME_LEN)


def main():
    print(__doc__.strip().split('\n')[0])
    test_gate_is_derived()
    test_lzss_against_pyff7()
    test_resolve()
    test_toml_subset()
    test_unknown_keys()
    test_section8_bake()
    test_bake_plan_and_verify()
    test_exception_table()
    print()
    if FAILURES:
        print('FAILED (%d)' % len(FAILURES))
        for f in FAILURES:
            print('   ' + f)
        return 1
    print('all good')
    return 0


if __name__ == '__main__':
    sys.exit(main())
