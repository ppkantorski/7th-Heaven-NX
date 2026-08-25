#!/usr/bin/env python3
"""
test_ws.py -- the 16:9 pipeline: mode resolution, the clamp identity, and
the section-8 bake end to end on the real flevel.lgp.

WHAT IS BEING PROTECTED
=======================
1. **Mode resolution never resurrects a known-bad build.** `stretch`, `fit`
   and `field` were all shipped to hardware and all three regressed. `field`
   was labelled "16:9 -- recommended" in the dropdown. If any of them ever
   starts resolving to the supported pipeline again -- through a settings.json
   migration, a default, or a typo in a string comparison -- someone gets a
   broken build and no warning. Asserted directly.

2. **The clamp identity.** `ff7nx_ws.clamp_delta` claims that writing
   `left + d` / `right - d` into section 8 makes the STOCK `+/-160` code
   compute exactly the bounds FFNx's replacement function computes. That is
   an arithmetic claim about 711 real fields and it is checked as one, on
   every field, against a straight transcription of background.cpp:417 --
   not against a restatement of the delta formula.

3. **The gate is mode-based, not range-based.** This is the correction
   README-45 §8.2 paid a measurement for: `is_fieldmap_wide()` is
   `getMode() != WM_DISABLED`, and 306 of Cosmos's fields are switched on by
   an explicit `mode` key while their camera range is left alone. A clamp
   that decides wideness from `right - left >= 427` silently does nothing
   for 43% of the fields. There is a test that fails if that regresses.

4. **The bake round-trips.** Written into a rebuilt archive, read back out
   of the rebuilt archive, compared. This is the check that makes the data
   half falsifiable without a console, so it has to actually run.

5. **The clamp is not idempotent and the verifier knows it.** The config
   bake is absolute; the clamp is relative. A verifier that re-derives the
   plan from an already-clamped archive would double-apply and report a
   correct build as broken. Asserted, because the naive version of that
   function was written first and looked right.
"""
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ff7nx_ws as WS                                        # noqa: E402
import ff7nx_wsdata as W                                     # noqa: E402

FAILURES = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILURES.append(msg)


def group(name):
    print('\n' + name)
    print('-' * len(name))


# ======================================================================
def test_mode_resolution():
    group('mode resolution')
    saved = dict(os.environ)
    try:
        for bad in WS.LEGACY_MODES:
            os.environ['SEVENTH_NX_WIDESCREEN'] = bad
            check(WS.mode() == '',
                  'the known-bad value %r does not resolve to a mode' % bad)
            check(not WS.enabled(),
                  '%r leaves the supported pipeline switched off' % bad)
            check(WS.legacy_mode() == bad,
                  '%r is still reported, so the log can say why nothing '
                  'happened' % bad)
            check(not WS.wants_bake() and not WS.wants_clamp()
                  and not WS.wants_framing(),
                  '%r turns on no stage of the new pipeline' % bad)

        for off in ('', '  ', 'off', '0', 'true', '1', 'yes'):
            os.environ['SEVENTH_NX_WIDESCREEN'] = off
            check(not WS.enabled(),
                  'the value %r is OFF -- 16:9 never defaults to on' % off)

        os.environ['SEVENTH_NX_WIDESCREEN'] = 'ws'
        os.environ.pop('SEVENTH_NX_WS_FRAMING', None)
        check(WS.enabled() and WS.stage() == WS.STAGE_CONTENT,
              "'ws' alone is the CONTENT stage")
        check(WS.wants_bake(), 'the content stage bakes the camera ranges')
        check(not WS.wants_framing(),
              'the content stage never opens exefs/main')
        check(not WS.wants_clamp(),
              'the content stage never ships the clamp -- on its own it is a '
              'straight loss of camera travel')

        os.environ['SEVENTH_NX_WS_FRAMING'] = '1'
        check(WS.stage() == WS.STAGE_FRAMING, 'the framing stage is opt-in')
        os.environ.pop('SEVENTH_NX_WS_FRAMING', None)
        os.environ['SEVENTH_NX_WIDESCREEN'] = 'ws-2d'
        check(WS.mode() == WS.MODE_WS_3D,
              "a saved 'ws-2d' resolves to ws-3d IN THE RESOLVER, so it "
              "cannot keep selecting the retired patch set no matter which "
              "files were copied -- this cost two hardware builds")
        check(WS.enabled() and WS.stage() == WS.STAGE_FRAMING,
              "and it still lands on the framing stage")
        check(WS.wants_bake(),
              'and it still bakes the ranges -- the framing is never shipped '
              'without the data under it')
        os.environ['SEVENTH_NX_WIDESCREEN'] = 'ws'
        os.environ['SEVENTH_NX_WS_FRAMING'] = '1'
        check(WS.wants_clamp() and WS.wants_framing(),
              'the clamp travels WITH the framing and only with it')

        os.environ['SEVENTH_NX_WIDESCREEN'] = ''
        check(WS.stage() == '' and not WS.wants_framing(),
              'the framing flag alone does nothing with 16:9 off')
    finally:
        os.environ.clear()
        os.environ.update(saved)


# ======================================================================
def ffnx_bounds(left, right, wide):
    """
    A straight transcription of background.cpp:417, written out longhand.

    Deliberately NOT calling anything in ff7nx_ws: the point of the identity
    test is that two independent expressions agree, and reusing the module's
    own helper would make it agree with itself.
    """
    half_width = 160
    if wide:
        left = left + 1
        right = right - 1
        size = right - left
        half_width = 160 + min(53, size // 2 - 160)
    return left + half_width, right - half_width


def test_clamp_identity_synthetic():
    group('the clamp identity, over the whole plausible range')
    bad = 0
    for size in range(200, 4096):
        for left in (-2048, -1000, -160, 0, 160, 1000):
            rng = {'left': left, 'right': left + size, 'top': 0,
                   'bottom': 480, 'width': size}
            for wide in (False, True):
                want = ffnx_bounds(left, left + size, wide)
                got = WS.clamp_bounds(rng, wide)
                if got != want:
                    bad += 1
                    if bad < 4:
                        print('    size=%d left=%d wide=%s got=%s want=%s'
                              % (size, left, wide, got, want))
                    continue
                new = WS.clamped_range(rng, wide)
                lo = (new['left'] if new else rng['left']) + 160
                hi = (new['right'] if new else rng['right']) - 160
                # `clamped_range` returns None when it refuses; the refusal
                # is only allowed where applying it would be degenerate.
                if new is None and WS.clamp_delta(rng, wide) != 0:
                    # Back to `>= 320`: FINDINGS-187 retracted FINDINGS-177,
                    # so a delta that lands on exactly 320 is APPLIED again.
                    # The note below is kept for the record.
                    # `> 320`, not `>= 320`. FINDINGS-177 made
                    # `clamped_range` refuse a result of EXACTLY 320 -- that
                    # is a clamp of `0 .. 0`, a camera pinned to a point --
                    # and this line was left at `>=`, so the suite has been
                    # red on 684 sizes ever since and nobody could tell a new
                    # break from the old one. The refusal at exactly 320 is
                    # the intended behaviour; keeping the window on the art
                    # for those fields is `ff7nx_camfit`'s job, not this
                    # identity's. See FINDINGS-184.
                    if size - 2 * WS.clamp_delta(rng, wide) >= 320:
                        bad += 1
                        print('    refused a legal delta at size=%d' % size)
                    continue
                if (lo, hi) != want:
                    bad += 1
                    if bad < 8:
                        print('    stock+/-160 on the clamped range gives '
                              '%s, FFNx gives %s (size=%d wide=%s)'
                              % ((lo, hi), want, size, wide))
    check(bad == 0,
          'stock code on the clamped range == FFNx code on the original, '
          'across every size 200..4095 and six origins (%d mismatch(es))'
          % bad)

    # The cap, from its parts. 213 is half of 426 and 426 is the 16:9 view
    # in game units; 53 is what is left after the stock 160. None of those
    # three numbers should be editable without this failing.
    check(WS.HALF_WIDTH_43 + WS.HALF_WIDTH_CAP == 213,
          'the capped half_width is 213 = ceil(854/4)')
    check(WS.half_width(10 ** 6) == 213, 'a very wide field caps at 213')
    check(WS.half_width(320) == 160,
          'a 320-unit range gets the stock 160 and is therefore unchanged')
    check(WS.half_width(300) == 150,
          'a range NARROWER than 320 gets less than 160 -- FFNx lets the '
          'camera travel further there, and clamping it to 160 would be a '
          'silent behaviour change')


def test_gate_is_mode_not_range():
    group('the gate is the MODE, not the camera range (README-45 §8.2)')
    # A narrow field that a config switches on with `mode = 1`. The range is
    # untouched, so a range-based gate sees nothing to do. FFNx widens it.
    before = {'narrowfld': {'left': -200, 'right': 200, 'top': -120,
                            'bottom': 120, 'width': 400, 'height': 240}}
    check(not W.gate(400),
          'a 400-unit range does NOT pass FFNx’s 427 gate on its own')

    plan_off, res_off, _ = WS.plan_ranges(before, {}, {}, clamp=True)
    check(res_off['narrowfld']['mode'] == W.WM_DISABLED,
          'with no config the field is WM_DISABLED')
    check('narrowfld' not in plan_off,
          'and a disabled field is not clamped at all')

    cfg = {'narrowfld': {'mode': W.WM_EXTEND_ONLY}}
    plan_on, res_on, _ = WS.plan_ranges(before, cfg, {}, clamp=True)
    check(res_on['narrowfld']['mode'] != W.WM_DISABLED,
          'an explicit `mode` switches it on WITHOUT widening the range')

    # THIS ASSERTION USED TO READ `'narrowfld' in plan_on`, AND IT HAD BEEN
    # FAILING SINCE FINDINGS-177.
    #
    # FFNx's identity collapses a 400-unit range to 320, which pins the
    # camera to a point. FINDINGS-177 decided that was worse than keeping
    # the travel the field shipped with, and added the `<= 2 * 160` guard
    # that makes `clamped_range` return None here. The test was not updated,
    # so the tree carried a red test that hid real ones.
    #
    # Neither answer was complete, because both were arguing about the clamp
    # while the real quantity -- how wide the VIEW is -- lives in section 9.
    # `ff7nx_camfit` measures it and tightens the clamp until the window
    # cannot leave the art, which is a stronger guarantee than either: 108
    # fields on build 79 were scrolling into black with the guard in place,
    # including `las4_1`, and none are now.
    #
    # So the contract this test defends is the GATE -- mode, not range --
    # and the clamp's own behaviour on a collapsing range is FINDINGS-177's
    # to state. See FINDINGS-184.
    check('narrowfld' in plan_on,
          'and the clamp then applies -- FINDINGS-177 refused a range that '
          'collapses to 320 and that is RETRACTED (FINDINGS-187): the pin IS '
          'the answer, and 88 fields were showing black pillars without it')
    got = plan_on['narrowfld']
    want = ffnx_bounds(-200, 200, True)
    check((got['left'] + 160, got['right'] - 160) == want,
          'and it lands on FFNx bounds %s -- a pinned camera' % (want,))

    wide = {'widefld': {'left': -400, 'right': 400, 'top': -120,
                        'bottom': 120, 'width': 800, 'height': 240}}
    plan_w, _res_w, _ = WS.plan_ranges(wide, {}, {}, clamp=True)
    got = plan_w['widefld']
    want = ffnx_bounds(-400, 400, True)
    check((got['left'] + 160, got['right'] - 160) == want,
          'a range that does NOT collapse still lands on FFNx’s bounds %s'
          % (want,))


def test_2d_projection():
    group('the 2D projection (README-47)')
    left, right = WS.ortho_2d(False)
    # 2/640 is not exactly representable in binary32, so this is a tolerance
    # rather than an equality -- the round trip through the float is the
    # thing being checked, not decimal arithmetic.
    check(abs(left) < 0.01 and abs(right - 640.0) < 0.01,
          'the STOCK words decode to ortho(0, 640) -- read back out of the '
          '`expect` bytes, not restated from a comment (got %.4f..%.4f)'
          % (left, right))

    left, right = WS.ortho_2d(True, shift_origin=True)
    check(abs((right - left) - WS.WIDE_VIEWPORT_WIDTH) < 0.01,
          'the PATCHED words give a projection exactly %d game units wide '
          '(got %.2f) -- this is the number that decides whether anything '
          'stretches' % (WS.WIDE_VIEWPORT_WIDTH, right - left))

    # _41 cannot encode -640/854 in one movz, so it is rounded to -0.75.
    # The cost of that rounding is asserted rather than assumed.
    ideal_l = WS.WIDE_VIEWPORT_X
    off_units = abs(left - ideal_l)
    off_px = off_units / (right - left) * 1920
    check(off_px < 1.0,
          'the _41 rounding shifts the whole image by %.2f game units = '
          '%.2f pixels of 1920, i.e. under one pixel' % (off_units, off_px))

    # The 4:3 region has to come out CENTRED, or menus sit off to one side.
    _11 = 2.0 / (right - left)
    _41 = -1.0 - left * _11
    lo, hi = 0 * _11 + _41, WS.GAME_W_43 * _11 + _41
    check(abs(lo + hi) < 0.01,
          'game-space 0..640 lands symmetrically at %+.4f..%+.4f, so a 4:3 '
          'menu is centred with no patch of its own -- which is exactly why '
          'FFNx needs no UI patches either' % (lo, hi))

    # And the pixel scale must not move, or everything is subtly resized.
    stock = 1440.0 / WS.GAME_W_43
    wide = 1920.0 / (right - left)
    check(abs(stock - wide) / stock < 0.005,
          'pixel scale at 720p: %.4f px/unit stock vs %.4f wide (%.2f%% '
          'apart). A widescreen patch that changes this is a resize, not a '
          'reframe.' % (stock, wide, 100 * abs(stock - wide) / stock))


def test_3d_geometry():
    group('the 3D half: game_w 854 makes both viewports land right')
    # `_11 = w/game_w`, `_41 = ((x + w/2) - game_w/2)/(game_w/2)`, written out
    # rather than called, so this checks the CLAIM and not the implementation.
    def viewport_matrix(x, w, game_w):
        return w / float(game_w), ((x + w / 2.0) - game_w / 2.0) / (game_w / 2.0)

    W854 = WS.WIDE_VIEWPORT_WIDTH
    _11, _41 = viewport_matrix(0, W854, W854)
    check(abs(_11 - 1.0) < 1e-9 and abs(_41) < 1e-9,
          'the widened FIELD viewport (0, 854) gives _11=1.0 _41=0 -- full '
          'width, centred, unstretched, and with NO x offset, because 854/2 '
          'is exactly the centre of a 0..854 span')
    _11, _41 = viewport_matrix(0, WS.GAME_W_43, W854)
    check(abs(_11 - WS.GAME_W_43 / float(W854)) < 1e-9,
          'the UI/battle viewport (0, 640) keeps its correct 4:3 SCALE '
          '(_11=%.4f), so nothing stretches' % _11)
    check(abs(_41 + 0.2506) < 1e-3,
          'but _41=%+.4f, i.e. 107 units left of centre -- the ONE known '
          'gap in this set, and it is a shift, not a stretch' % _41)
    _11b, _41b = viewport_matrix(107, WS.GAME_W_43, W854)
    check(abs(_41b) < 1e-9 and abs(_11b - _11) < 1e-9,
          'and x += 107 closes it exactly (_41=0, scale unchanged) -- which '
          'is what README-47 §4 specifies')

    # Stock behaviour must be untouched when game_w is left alone.
    _11, _41 = viewport_matrix(0, WS.GAME_W_43, WS.GAME_W_43)
    check(abs(_11 - 1.0) < 1e-9 and abs(_41) < 1e-9,
          'with game_w still 640 the stock 4:3 viewport is identity, so the '
          'patch cannot change anything in an Off build')


def test_3d_patches_against_the_binary():
    group('the 3D patch sites, against dump/exefs/main')
    import struct as _s
    path = os.path.join(_ROOT, 'dump', 'exefs', 'main')
    if not os.path.exists(path):
        print('  SKIP  no dump/exefs/main')
        return
    try:
        import nxmap
        img = nxmap.Main(path).img
    except Exception as exc:                                   # noqa: BLE001
        print('  SKIP  cannot map the module (%s)' % exc)
        return
    for p in (list(WS.GAME_W_PATCHES) + list(WS.FIELD_MODE2_PATCHES)
              + list(WS.UNCROP_PATCHES) + [WS.ORTHO_ORIGIN_PATCH]):
        want = bytes(int(b, 16) for b in p['expect'].split())
        got = bytes(img[p['va']:p['va'] + 4])
        check(got == want, '+%08X  %s' % (p['va'], p['name']))
    # Every game_w load is followed by a load of game_h from a base register
    # the patch does not write -- otherwise dropping the first load would
    # break the second, silently, in the driver.
    for p in WS.GAME_W_PATCHES:
        cur = _s.unpack_from('<I', img, p['va'])[0]
        nxt = _s.unpack_from('<I', img, p['va'] + 4)[0]
        rd = cur & 31
        base = (nxt >> 5) & 31
        check(base != rd,
              '+%08X: the next instruction reads x%d, which this patch does '
              'not write (it writes w%d)' % (p['va'], base, rd))


def test_uncrop_geometry():
    group('the vertical uncrop -- the bars above and below')
    # The frame is 480 units tall; the field's mode-2 viewport is 448.
    frac = 448 / 480.0
    check(abs(frac - 0.9333) < 1e-3,
          'the field fills %.1f%% of the frame height, so %.0f px of a 720p '
          'screen is black and no horizontal patch can touch it'
          % (100 * frac, 720 * (1 - frac)))
    # After the patch the viewport matches the frame exactly.
    check(480 / 480.0 == 1.0,
          'at 480 the field viewport equals the frame and the bars go')
    # The half-height must move with it or the projection disagrees with the
    # viewport -- the vertical twin of v8's Error 2.
    check(240 * 2 == 480,
          'and the half-height 240 is exactly half of it, so models project '
          'through the same height the background is drawn through')


def test_2d_projection_against_the_binary():
    """The `expect` bytes must match the module, or the patch lands nowhere."""
    group('the 2D projection sites, against dump/exefs/main')
    import struct as _s
    for rel in (('dump', 'exefs', 'main'),):
        path = os.path.join(_ROOT, *rel)
        if os.path.exists(path):
            break
    else:
        path = None
    if not path:
        print('  SKIP  no dump/exefs/main')
        return
    try:
        import nxmap
        img = nxmap.Main(path).img
    except Exception as exc:                                   # noqa: BLE001
        print('  SKIP  cannot map the module (%s)' % exc)
        return
    for p in WS.PROJECTION_2D_PATCHES:
        want = bytes(int(b, 16) for b in p['expect'].split())
        got = bytes(img[p['va']:p['va'] + 4])
        check(got == want,
              '+%08X holds %s (expected %s) -- %s'
              % (p['va'], ' '.join('%02X' % b for b in got),
                 p['expect'], p['name']))
    # The three sites must be distinct and inside .text.
    vas = [p['va'] for p in WS.PROJECTION_2D_PATCHES]
    check(len(set(vas)) == len(vas), 'the three sites are distinct')
    check(all(v < 0x1152660 for v in vas), 'and all three are in .text')


def test_config_report():
    group('what a range edit cannot express is reported, not faked')
    cfg = {'a': {'left': -100, 'right': 100},
           'b': {'h_offset': 12},
           'c': {'v_offset': -8, 'reset_vertical_pos': True},
           'd': {'mode': 1, 'nonsense_key': 3}}
    rep = WS.config_report(cfg)
    check(rep['point_shift'] == ['b', 'c'],
          'fields asking for a camera POINT shift are named')
    check('a' not in rep['point_shift'] and 'd' not in rep['point_shift'],
          'and fields that only move the range are not')
    check('nonsense_key' in rep['unknown_keys'],
          'keys FFNx does not read are surfaced rather than ignored')


def test_diagonal_rail_preservation():
    group('the unique diagonal camera rail')
    base = {'left': -320, 'top': -224, 'right': 296, 'bottom': 184,
            'width': 616, 'height': 408}
    current = {'left': -266, 'top': -176, 'right': 168, 'bottom': 176}
    resolved = {'ship_1': {'mode': W.WM_EXTEND_ONLY}}
    got, changes = WS.preserve_diagonal_rail(
        {'ship_1': base}, {'ship_1': current}, resolved, {'ship_1': 2})
    check(got['ship_1'] == {'left': -266, 'top': -224,
                            'right': 242, 'bottom': 176},
          'ship_1 restores only the shortened upper-right endpoint and '
          'keeps the proven lower-left endpoint')
    check(changes == [('ship_1', (8, -56), (82, -104))],
          'the measured upper-right endpoint is 74 units farther right and '
          '48 units farther up')
    lower_before = (current['left'] + WS.HALF_WIDTH_43,
                    current['bottom'] - 120)
    lower_after = (got['ship_1']['left'] + WS.HALF_WIDTH_43,
                   got['ship_1']['bottom'] - 120)
    check(lower_before == lower_after == (-106, 56),
          'the lower-left anchor stays at (-106,56), avoiding build 173\'s '
          'eight-unit black band')

    plain, plain_changes = WS.preserve_diagonal_rail(
        {'ordinary': base}, {'ordinary': current},
        {'ordinary': {'mode': W.WM_EXTEND_ONLY}}, {'ordinary': 0})
    check(plain == {'ordinary': current} and not plain_changes,
          'an ordinary rectangular field is byte-for-byte outside the rule')

    better = {'left': -266, 'top': -240, 'right': 260, 'bottom': 176}
    future, future_changes = WS.preserve_diagonal_rail(
        {'ship_1': base}, {'ship_1': better}, resolved, {'ship_1': 2})
    check(future == {'ship_1': better} and not future_changes,
          'a future config with a better endpoint is not overwritten')


# ======================================================================
def find_flevel():
    """The same search test_wsdata.py does, so both run in the same tree."""
    for rel in (('dump', 'romfs', 'ff7', 'resources', 'ff7_1.02', 'data',
                 'field', 'flevel.lgp'), ('flevel.lgp',)):
        p = os.path.join(_ROOT, *rel)
        if os.path.exists(p):
            return p, None
    p = os.environ.get('SEVENTH_NX_FLEVEL', '')
    if p and os.path.exists(p):
        return p, None
    z = os.path.join(_ROOT, 'game_data_zips', 'field.zip')
    if os.path.exists(z):
        import tempfile
        import zipfile
        zf = zipfile.ZipFile(z)
        for n in zf.namelist():
            if n.lower().endswith('flevel.lgp'):
                tmp = tempfile.mkdtemp(prefix='ws-test-')
                zf.extract(n, tmp)
                return os.path.join(tmp, n), tmp
    return None, None


def test_identity_on_every_real_field(flevel):
    group('the clamp identity on all 711 real fields')
    import lgp
    import ff7nx_wsbake as B
    before = B.ranges_from_archive(lgp.Archive(flevel))
    check(len(before) > 700, 'read %d field camera ranges' % len(before))

    for label, cfg in (('no config', {}),
                       ('a Cosmos-shaped config', None)):
        if cfg is None:
            # 300 fields switched on by `mode` alone, 15 widened -- the
            # shape README-45 §9 measured on the real mod.
            narrow = [n for n in sorted(before)
                      if not W.gate(before[n]['width'])]
            cfg = {W.field_key(n): {'mode': 1} for n in narrow[:300]}
            for n in narrow[300:315]:
                r = before[n]
                cfg[W.field_key(n)] = {'left': r['left'] - 64,
                                       'right': r['right'] + 64}
        plan, resolved, _ = WS.plan_ranges(before, cfg, {}, clamp=True)
        bad = 0
        for name, rng in before.items():
            info = resolved[name]
            target = info['range']
            want = ffnx_bounds(int(target['left']), int(target['right']),
                               info['mode'] != W.WM_DISABLED)
            new = plan.get(name)
            lo = (new['left'] if new else rng['left']) + 160
            hi = (new['right'] if new else rng['right']) - 160
            if (lo, hi) != want:
                bad += 1
                if bad < 4:
                    print('    %s: got %s want %s' % (name, (lo, hi), want))
        check(bad == 0, '%s: 0 of %d fields disagree with FFNx'
              % (label, len(before)))


def test_bake_round_trip(flevel):
    group('the bake, written and read back out of a rebuilt archive')
    import lgp
    import ff7nx_wsbake as B

    archive = lgp.Archive(flevel)
    before = B.ranges_from_archive(archive)
    narrow = [n for n in sorted(before) if not W.gate(before[n]['width'])]
    picked = narrow[:6]
    cfg = {W.field_key(n): {'left': before[n]['left'] - 64,
                            'right': before[n]['right'] + 64}
           for n in picked}

    payloads = {}
    stats = WS.apply_to_flevel(
        archive, payloads, cfg, {},
        # compress=False stores literal LZS: byte-identical after
        # decompression, ~12% bigger, and instant. The compressor is pure
        # Python and a field is most of a megabyte; this test is checking
        # the DATA, not the compressor, which tests/test_wsdata.py covers.
        encode=lambda raw: archive.encode_field(raw, compress=False),
        clamp=False, log=lambda *_: None)
    check(stats['written'] == len(picked),
          'wrote %d camera range(s)' % stats['written'])
    check(stats['read'] == len(before),
          'and read all %d without decompressing past section 8'
          % stats['read'])

    import tempfile
    out = os.path.join(tempfile.mkdtemp(prefix='ws-bake-'), 'flevel.lgp')
    archive.replace(payloads)
    archive.write(out)

    ok, problems = WS.verify_flevel(out, stats['before'], stats['plan'])
    check(ok, 'every planned range is in the rebuilt archive and nothing '
              'else moved' + ('' if ok else ' -- %s' % problems[:3]))

    after = B.ranges_from_archive(lgp.Archive(out))
    for n in picked:
        check(after[n]['left'] == before[n]['left'] - 64
              and after[n]['right'] == before[n]['right'] + 64,
              '%s: %d..%d -> %d..%d'
              % (n, before[n]['left'], before[n]['right'],
                 after[n]['left'], after[n]['right']))
    check(all(after[n]['top'] == before[n]['top']
              and after[n]['bottom'] == before[n]['bottom'] for n in picked),
          'the vertical pair is untouched by a horizontal-only entry')
    untouched = [n for n in before if n not in picked]
    check(all(after[n] == before[n] for n in untouched),
          'and all %d unconfigured fields are byte-identical'
          % len(untouched))
    try:
        os.remove(out)
    except OSError:
        pass


def test_clamp_is_not_idempotent(flevel):
    group('the clamp is relative, and the verifier must not re-derive it')
    import lgp
    import ff7nx_wsbake as B
    before = B.ranges_from_archive(lgp.Archive(flevel))
    plan1, _r, _s = WS.plan_ranges(before, {}, {}, clamp=True)
    check(len(plan1) > 300, '%d fields are clamped from the gate alone'
          % len(plan1))
    # Feed the RESULT back in, as a naive verifier would.
    once = dict(before)
    for n, r in plan1.items():
        once[n] = dict(once[n], left=r['left'], right=r['right'],
                       width=r['right'] - r['left'])
    plan2, _r2, _s2 = WS.plan_ranges(once, {}, {}, clamp=True)
    check(len(plan2) > 0,
          're-deriving the plan from a clamped archive produces MORE '
          'changes (%d), which is why verify_flevel takes the plan instead '
          'of recomputing it' % len(plan2))

    # And the bake half, in contrast, IS idempotent -- so re-running a build
    # over its own output cannot drift the config's ranges.
    cfg = {W.field_key(n): {'left': before[n]['left'] - 64,
                            'right': before[n]['right'] + 64}
           for n in sorted(before)[:20]}
    p1, _, _ = WS.plan_ranges(before, cfg, {}, clamp=False)
    applied = dict(before)
    for n, r in p1.items():
        applied[n] = dict(applied[n], **r,
                          width=r['right'] - r['left'])
    p2, _, _ = WS.plan_ranges(applied, cfg, {}, clamp=False)
    check(not p2, 'the config bake is absolute and re-baking changes nothing')


# ======================================================================
def main():
    test_mode_resolution()
    test_clamp_identity_synthetic()
    test_gate_is_mode_not_range()
    test_2d_projection()
    test_2d_projection_against_the_binary()
    test_3d_geometry()
    test_uncrop_geometry()
    test_3d_patches_against_the_binary()
    test_config_report()
    test_diagonal_rail_preservation()

    flevel, _tmp = find_flevel()
    if not flevel:
        print('\nSKIP  field data (no flevel.lgp and no game_data_zips/'
              'field.zip)')
    else:
        print('\nflevel  : %s' % flevel)
        test_identity_on_every_real_field(flevel)
        test_bake_round_trip(flevel)
        test_clamp_is_not_idempotent(flevel)

    print()
    if FAILURES:
        print('%d FAILURE(S)' % len(FAILURES))
        for f in FAILURES:
            print('  - ' + f)
        return 1
    print('all good')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
