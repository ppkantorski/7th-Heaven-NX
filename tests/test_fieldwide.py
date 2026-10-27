#!/usr/bin/env python3
"""
Tests for ff7nx_fieldwide.

The interesting ones are the last two groups: the parallax patches are
checked against the REAL module rather than against a restatement of the
constants, and the camera-range identity is proved against every field in
the REAL flevel.lgp rather than a handful of made-up numbers.

Both of those degrade to a skip when the game data is not to hand, so this
still runs on a bare checkout -- but it says so loudly rather than passing
quietly, because a green run that checked nothing is how the widescreen
work lost three hardware builds.

Both paths are found automatically from a normal project layout, including
`flevel.lgp` inside `game_data_zips/field.zip`, so the usual invocation is
just:

    python3 tests/test_fieldwide.py

    python3 tests/test_fieldwide.py [path/to/main] [path/to/flevel.lgp|.zip]

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
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import ff7nx_fieldwide as fw  # noqa: E402

FAILURES = []
CHECKS = [0]
SKIPS = []
_TMP = []


# ------------------------------------------------------------ discovery
#
# Ordered pristine-first. `sdout/` is the BUILT tree, so its `main` already
# has the 60 FPS patches in it and its flevel.lgp already has whatever mods
# were enabled -- fine for a sanity check, wrong as the default baseline,
# because the parallax patches' `expect` words are the stock ones.
TITLE_ID = '0100A5B00BDC6000'
SDOUT = 'sdout/atmosphere/contents/%s/romfs/ff7/workingdir' % TITLE_ID

MAIN_CANDIDATES = (
    'dump/exefs/main',
    'exefs/main',
    'sdout/atmosphere/contents/%s/exefs/main' % TITLE_ID,
)

# A loose file beats unpacking 141 MB of field.zip, so the zip is last.
FLEVEL_CANDIDATES = (
    'dump/romfs/ff7/workingdir/data/field/flevel.lgp',
    'workingdir/data/field/flevel.lgp',
    SDOUT + '/data/field/flevel.lgp',
    'flevel.lgp',
    'game_data_zips/field.zip',
)


def _resolve(candidates, given=None, what=''):
    """
    `given` if it exists, else the first candidate that does. Or None.

    A `given` path that does not exist falls back to discovery rather than
    giving up, and says so. Silently skipping because of a typo'd path is
    exactly the failure mode this whole test suite exists to avoid.
    """
    if given:
        if os.path.exists(given):
            return given
        SKIPS.append('%s: %s does not exist, looked for %s elsewhere'
                     % (what, given, what))
    for rel in candidates:
        for base in ('.', ROOT):
            p = os.path.join(base, rel)
            if os.path.exists(p):
                return p
    return None


def _as_flevel(path):
    """
    A filesystem path to flevel.lgp, unpacking from a zip if needed.

    Returns None (and records a SKIP reason) if the zip has no flevel.lgp.
    Extracted files land in a temp dir cleaned up at exit.
    """
    if path is None or not zipfile.is_zipfile(path):
        return path
    with zipfile.ZipFile(path) as z:
        member = next((n for n in z.namelist()
                       if os.path.basename(n).lower() == 'flevel.lgp'), None)
        if member is None:
            SKIPS.append('%s contains no flevel.lgp' % path)
            return None
        d = tempfile.TemporaryDirectory()
        _TMP.append(d)
        z.extract(member, d.name)
        return os.path.join(d.name, member)


def check(cond, what):
    CHECKS[0] += 1
    if not cond:
        FAILURES.append(what)


def eq(got, want, what):
    check(got == want, '%s: got %r want %r' % (what, got, want))


# ------------------------------------------------------------ constants
def test_constants():
    eq(fw.HALF_WIDTH_169, 213, 'half_width 16:9 == ceil(854/4)')
    eq(fw.LEFT_OFFSET_169, 459, 'left_offset 16:9 == 352 + 107')
    eq(fw.WIDE_FIELD_MIN_RANGE, 427, 'wide gate == 640/2 + |-107|')
    eq(fw.HALF_WIDTH_CAP_DELTA, 53, "FFNx's min() cap")
    # 160 and 213 are each half of their view; that relationship is the
    # whole justification for the patch, so assert it rather than the
    # literals alone.
    eq(fw.HALF_WIDTH_43 * 2, 320, '4:3 view is 320 units')
    eq(fw.HALF_WIDTH_169 * 2 + 1, fw.WIDE_VIEWPORT_WIDTH // 2,
       '16:9 view is 427 units (213*2 rounded down by the int divide)')


# ------------------------------------------------------------ half_width
def test_half_width():
    # background.cpp:433 -- 160 + min(53, size/2 - 160)
    eq(fw.half_width(320), 160, 'size 320 -> exactly 160')
    eq(fw.half_width(424), 212, 'size 424 -> 212')
    eq(fw.half_width(425), 212, 'size 425 -> 212')
    eq(fw.half_width(426), 213, 'size 426 -> 213, the cap engages')
    eq(fw.half_width(2048), 213, 'large size stays capped at 213')
    for size in range(426, 4000):
        if fw.half_width(size) != 213:
            check(False, 'half_width(%d) left the cap' % size)
            break

    # half_width() takes the ALREADY-ADJUSTED size, so the only camera range
    # that ends up on 212 is the one at the gate exactly: 427 - 2 == 425.
    # Getting this backwards is easy and would silently shift one field.
    eq(fw.half_width(427 - 2), 212, 'range 427 -> half 212')
    eq(fw.half_width(428 - 2), 213, 'range 428 -> half 213')


def test_gate_is_not_the_min():
    # The handoff's model treated the min() as the gate, which would apply
    # half_width < 160 to narrow fields. FFNx never does that.
    for size in (100, 200, 319, 320, 400, 426):
        left, right = 0, size
        check(not fw.is_wide(left, right), 'size %d must not qualify' % size)
        lo, hi = fw.ffnx_clip_bounds(left, right)
        eq((lo, hi), (left + 160, right - 160),
           'narrow field %d keeps stock bounds' % size)
        eq(fw.narrowed_range(left, right), None,
           'narrow field %d is not transformed' % size)
    check(fw.is_wide(0, 427), 'size 427 qualifies')
    check(not fw.is_wide(0, 426), 'size 426 does not')


# ------------------------------------------------------ the identity
def test_identity_synthetic():
    """stock code on transformed data == FFNx on stock data."""
    for size in range(427, 3000):
        left, right = -size // 2, size - size // 2
        got = fw.narrowed_range(left, right)
        check(got is not None, 'size %d should transform' % size)
        if got is None:
            continue
        l, r = got
        stock = (l + fw.HALF_WIDTH_43, r - fw.HALF_WIDTH_43)
        eq(stock, fw.ffnx_clip_bounds(left, right),
           'identity at size %d' % size)


def test_transform_is_symmetric_and_shrinking():
    for size in (427, 500, 1088, 2048):
        left, right = 0, size
        l, r = fw.narrowed_range(left, right)
        eq(l - left, right - r, 'transform is symmetric at %d' % size)
        check(l > left and r < right, 'transform narrows at %d' % size)


def test_guards_are_unreachable_by_construction():
    """
    The two safety guards in narrowed_range() cannot fire for any input
    that passes the gate. Rather than testing them with impossible inputs,
    prove the property they are guarding, so that if someone later loosens
    the gate the failure lands here.

      * int16: the transform only ever moves edges INWARD, so neither edge
        can leave the range the originals already occupied.
      * travel: r - l bottoms out at exactly 320, never below it.
    """
    worst = None
    for size in range(fw.WIDE_FIELD_MIN_RANGE, 6000):
        for left in (-32768 + size, -1000, 0, 1000, 32767 - size):
            right = left + size
            got = fw.narrowed_range(left, right)
            check(got is not None,
                  'gate passed but transform refused at %d/%d' % (left, size))
            if got is None:
                continue
            l, r = got
            check(left < l <= r < right, 'edges moved inward at %d' % size)
            check(fw.INT16_MIN <= l and r <= fw.INT16_MAX,
                  'stays in int16 at %d/%d' % (left, size))
            if worst is None or r - l < worst:
                worst = r - l
    eq(worst, 2 * fw.HALF_WIDTH_43,
       'narrowest surviving range is exactly 320 units')

    # Below the gate nothing is transformed at all.
    eq(fw.narrowed_range(0, 200), None, 'sub-gate range not transformed')
    eq(fw.narrowed_range(0, 426), None, 'one below the gate not transformed')


def test_widen_section8_touches_only_camera_range():
    body = bytes(range(256)) * 4
    s8 = bytearray(body)
    struct.pack_into('<hhhh', s8, 0x0C, 0, -100, 1000, 100)   # size 1000
    out = fw.widen_section8(bytes(s8))
    check(out is not None, 'a wide field transforms')
    eq(len(out), len(s8), 'length unchanged')
    diff = [i for i in range(len(out)) if out[i] != s8[i]]
    check(all(0x0C <= i < 0x14 for i in diff),
          'only camera_range bytes changed, got %r' % diff)
    l, t, r, b = struct.unpack_from('<hhhh', out, 0x0C)
    eq((t, b), (-100, 100), 'top/bottom untouched -- vertical is uncrop, not us')

    # a narrow field is left completely alone
    struct.pack_into('<hhhh', s8, 0x0C, 0, -100, 300, 100)
    eq(fw.widen_section8(bytes(s8)), None, 'narrow field returns None')

    eq(fw.widen_section8(b'\x00' * 4), None, 'truncated section refused')


# --------------------------------------------- part 1, against real main
def test_parallax_patches_against_module(main_path):
    """
    Every `expect` must be the word actually in `main` at that address, and
    every `set` must decode to the same instruction with only the immediate
    moved. Checked against the module, not against a comment.
    """
    if not main_path or not os.path.exists(main_path):
        SKIPS.append('parallax patches vs real main (no module given)')
        return
    try:
        import nxmap
    except ImportError as exc:
        SKIPS.append('parallax patches vs real main (%s)' % exc)
        return
    m = nxmap.Main(main_path)

    def word(s):
        return struct.unpack('<I', bytes(int(b, 16) for b in s.split()))[0]

    for p in fw.PARALLAX_PATCHES:
        va = p['va']
        actual = struct.unpack_from('<I', m.text, va)[0]
        eq('%08X' % actual, '%08X' % word(p['expect']),
           '%s: module word at +%#x' % (p['name'], va))

        old, new = word(p['expect']), word(p['set'])
        # same opcode, same registers -- only the imm12 field may differ
        mask = ~(0xFFF << 10) & 0xFFFFFFFF
        eq(old & mask, new & mask,
           '%s: only the immediate moves' % p['name'])
        old_imm = (old >> 10) & 0xFFF
        new_imm = (new >> 10) & 0xFFF
        if old_imm == fw.LEFT_OFFSET_43:
            eq(new_imm, fw.LEFT_OFFSET_169, '%s: 352 -> 459' % p['name'])
        elif old_imm == fw.HALF_WIDTH_43:
            eq(new_imm, fw.HALF_WIDTH_169, '%s: 160 -> 213' % p['name'])
        else:
            check(False, '%s: unexpected original immediate %d'
                  % (p['name'], old_imm))
        # never touch the uncrop constants
        check(old_imm not in (256, 112),
              '%s: must not patch an uncrop constant' % p['name'])

    vas = [p['va'] for p in fw.PARALLAX_PATCHES]
    eq(len(set(vas)), len(vas), 'no duplicate patch addresses')
    # all five must land inside the two functions they claim to
    for va in vas:
        check(0xA07780 <= va < 0xA095B0,
              'patch +%#x is inside layer3/layer4 pick_tiles' % va)


def test_patch_applies_cleanly(main_path, tmp_out):
    if not main_path or not os.path.exists(main_path):
        SKIPS.append('nso_patcher round trip (no module given)')
        return
    try:
        import nso_patcher  # noqa: F401
    except ImportError as exc:
        SKIPS.append('nso_patcher round trip (%s)' % exc)
        return
    lines = []
    ok = fw.apply_to_nso(main_path, tmp_out, log=lines.append)
    check(ok, 'apply_to_nso succeeded: %s' % '\n'.join(lines))
    if not ok:
        return
    import nxmap
    after = nxmap.Main(tmp_out)
    for p in fw.PARALLAX_PATCHES:
        want = struct.unpack('<I', bytes(int(b, 16)
                                         for b in p['set'].split()))[0]
        got = struct.unpack_from('<I', after.text, p['va'])[0]
        eq('%08X' % got, '%08X' % want,
           '%s: written into the module' % p['name'])
    # and nothing else in .text moved
    before = nxmap.Main(main_path)
    moved = sum(1 for i in range(0, len(before.text), 4)
                if before.text[i:i + 4] != after.text[i:i + 4])
    eq(moved, len(fw.PARALLAX_PATCHES),
       '.text diff is exactly the patch set')


# ------------------------------------------- part 2, against real flevel
def test_identity_against_real_flevel(flevel_path):
    if not flevel_path or not os.path.exists(flevel_path):
        SKIPS.append('camera-range identity vs real flevel.lgp (not given)')
        return
    try:
        rows = fw.survey(flevel_path)
    except Exception as exc:
        SKIPS.append('camera-range identity vs real flevel.lgp (%s)' % exc)
        return
    check(len(rows) > 600, 'survey found %d fields' % len(rows))
    rep = fw.equivalence_report(rows)
    eq(rep['clip_mismatch'], 0,
       'field_clip bounds reproduced exactly on every field')
    eq(rep['layer3_worse'], 0,
       'layer3 bounds never off by more than the documented 1')
    eq(rep['refused'], 0, 'no real field needs refusing')
    check(rep['wide'] > 0, 'some fields qualify (%d)' % rep['wide'])
    # the documented disagreement: present, and exactly 1 unit
    eq(rep['layer3_exact'] + rep['layer3_off_by_one'], rep['wide'],
       'every wide field is exact or off by one')
    # and the cost is the 108 units per field measured in v5
    if rep['wide']:
        eq(rep['travel_lost'] // rep['wide'], 108,
           'mean travel given up is 108 units, per README-widescreen-v5')


def test_roundtrip_through_lgp(flevel_path):
    """Transforming a field must leave every other section byte-identical."""
    if not flevel_path or not os.path.exists(flevel_path):
        SKIPS.append('lgp round trip (no flevel.lgp given)')
        return
    try:
        import lgp
        a = lgp.Archive(flevel_path)
    except Exception as exc:
        SKIPS.append('lgp round trip (%s)' % exc)
        return
    done = 0
    for e in a.entries:
        if done >= 25:
            break
        if not a.is_field(e):
            continue
        try:
            secs = lgp.split_sections(a.decompressed(e))
        except Exception:
            continue
        new8 = fw.widen_section8(secs[7])
        if new8 is None:
            continue
        done += 1
        rebuilt = lgp.split_sections(
            lgp.join_sections(secs[:7] + [new8] + secs[8:]))
        for i in range(9):
            if i == 7:
                continue
            eq(rebuilt[i], secs[i],
               '%s: section %d untouched' % (e['name'], i + 1))
        eq(len(rebuilt[7]), len(secs[7]),
           '%s: section 8 same length' % e['name'])
    check(done > 0, 'round trip exercised %d field(s)' % done)


def main():
    given_main = sys.argv[1] if len(sys.argv) > 1 else None
    given_flevel = sys.argv[2] if len(sys.argv) > 2 else None

    main_path = _resolve(MAIN_CANDIDATES, given_main, 'main')
    flevel_src = _resolve(FLEVEL_CANDIDATES, given_flevel, 'flevel.lgp')
    flevel = _as_flevel(flevel_src)

    print('main    : %s' % (main_path or '-- not found --'))
    print('flevel  : %s%s' % (flevel_src or '-- not found --',
                              ' (unpacked)' if flevel_src != flevel else ''))
    print()

    test_constants()
    test_half_width()
    test_gate_is_not_the_min()
    test_identity_synthetic()
    test_transform_is_symmetric_and_shrinking()
    test_guards_are_unreachable_by_construction()
    test_widen_section8_touches_only_camera_range()
    test_parallax_patches_against_module(main_path)
    test_identity_against_real_flevel(flevel)
    test_roundtrip_through_lgp(flevel)

    import tempfile
    with tempfile.TemporaryDirectory() as d:
        test_patch_applies_cleanly(main_path, os.path.join(d, 'main'))

    for s in SKIPS:
        print('SKIP  %s' % s)
    if FAILURES:
        for f in FAILURES:
            print('FAIL  %s' % f)
        print('\n%d/%d checks failed' % (len(FAILURES), CHECKS[0]))
        return 1
    print('\n%d checks passed%s'
          % (CHECKS[0], ', %d group(s) skipped' % len(SKIPS) if SKIPS else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
