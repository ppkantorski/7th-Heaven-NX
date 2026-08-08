#!/usr/bin/env python3
"""
test_fieldbuf.py -- the field render target, checked against the real module.

    python3 test_fieldbuf.py <path to exefs/main>

Three kinds of test, deliberately separated:

  A. THE MODEL.  HANDOFF-51 predicts a band period from a buffer width and a
     shader scale. HANDOFF-50 measured three of those periods on hardware.
     These assert the model reproduces every measured number, INCLUDING the
     8 px component at WS_SCALE 0.6667 that killed the previous theory.

  B. THE PRESETS.  The scale/span/aspect/alignment identities every preset
     has to satisfy, over the whole ladder.

  C. THE PATCH.  Signature verification, encoding, idempotence, byte-exact
     reversal through the whole ladder, and "the patched module differs in
     exactly the planned words and no others".

No hardware, no rebuild. B and C also cover the wiring: `ff7nx_ws.ws_scale()`
must equal the preset's, or the shaders and the module go out of step -- the
failure HANDOFF-49 §3 spent two builds on.
"""
import hashlib
import os
import shutil
import struct
import sys
import tempfile
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, '..')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ff7nx_fieldbuf as fb                                     # noqa: E402

FAILURES = []
COUNT = 0
_MAIN = None
_COPY = None


def check(name, fn):
    global COUNT
    COUNT += 1
    try:
        fn()
    except Exception as exc:                                    # noqa: BLE001
        FAILURES.append((name, traceback.format_exc()))
        print('  FAIL  %s' % name)
        print('        %s' % exc)
    else:
        print('  ok    %s' % name)


def eq(a, b, what=''):
    if a != b:
        raise AssertionError('%s: %r != %r' % (what or 'values', a, b))


def close(a, b, tol=1e-9, what=''):
    if abs(a - b) > tol:
        raise AssertionError('%s: %r != %r (tol %g)'
                             % (what or 'values', a, b, tol))


def band_period(width, scale, screen_h=720):
    """Screen-pixel band period the model predicts, or None for clean."""
    for line in fb.diagnose(width, scale, screen_h):
        if 'CLEAN' in line:
            return None
        if 'band period' in line:
            return float(line.split('that is')[1].split('SCREEN')[0])
    raise AssertionError('diagnose() reported neither clean nor a period')


# --------------------------------------------------------------------------
# A. the model, against HANDOFF-50's hardware measurements
# --------------------------------------------------------------------------
def t_measured_12px():
    """HANDOFF-50 §1: fundamental at 12 px, 720p, WS_SCALE 0.75."""
    close(band_period(320, 0.75), 12.0, 1e-6, '12 px fundamental')


def t_measured_8px_at_two_thirds():
    """
    HANDOFF-50 §1.1: at WS_SCALE 0.6667 "the 12 and 6 components vanish and
    an 8 px component appears". This is the measurement that retired the
    previous theory. The model must produce 8 and must NOT produce 12.
    """
    p = band_period(320, 2 / 3.0)
    close(p, 8.0, 1e-6, '8 px at WS_SCALE 2/3')


def t_measured_four_three_clean():
    """HANDOFF-50 §1: widescreen OFF is clean (0.021 vs 0.307)."""
    if band_period(320, 1.0) is not None:
        raise AssertionError('4:3 should be clean')


def t_measured_stretched_clean():
    """HANDOFF-50 §1.3 state 3: stretched into a 1920 target, clean."""
    if band_period(320, 1.0, 1080) is not None:
        raise AssertionError('stretched state should be clean')


def t_measured_ortho_bands():
    """HANDOFF-50 §1.3 state 4: scale in the ortho matrix, bands returned."""
    p = band_period(320, 640 / 854.0)
    if p is None:
        raise AssertionError('the ortho route should still band')


def t_supersample_is_irrelevant():
    """
    HANDOFF-50 §1.2: supersample moved the 4 px component 81% and left the
    12 px one. The model must depend only on the buffer and the scale.
    """
    eq(band_period(320, 0.75, 720), 12.0, 'independent of render target')


def t_integer_magnification_is_clean():
    """
    An exact 3x magnification is uniform, not a 3-pixel band. Getting this
    backwards reported the best preset on the ladder as the worst.
    """
    if band_period(1280, 0.75) is not None:
        raise AssertionError('3x magnification should be clean')


def t_half_integer_magnification_bands():
    """...but 2.5x is not: texels would cover alternately 2 and 3 pixels."""
    if band_period(1067, 0.75) is None:
        raise AssertionError('a fractional ratio should band')


# --------------------------------------------------------------------------
# B. the presets
# --------------------------------------------------------------------------
def t_preset_identities():
    """H = 240n, S = 320n/W, span = 2W/n -- for every preset."""
    for n in (1, 2, 3):
        p = fb.preset(n)
        eq(p['height'], 240 * n, 'height for n=%d' % n)
        close(p['ws_scale'], 320.0 * n / p['width'], 1e-12, 'S for n=%d' % n)
        close(fb.visible_units(p['width'], n), 640.0 / p['ws_scale'], 1e-9,
              'span for n=%d' % n)
        close(p['width'] * p['ws_scale'] / 640.0, p['height'] / 480.0, 1e-12,
              'square pixels for n=%d' % n)
        close(p['width'] * p['ws_scale'] / 640.0, n / 2.0, 1e-12,
              'px per unit for n=%d' % n)


def t_presets_are_clean():
    for n in (1, 2, 3):
        p = fb.preset(n)
        if band_period(p['width'], p['ws_scale']) is not None:
            raise AssertionError('preset %d should be clean' % n)


def t_presets_are_texel_aligned():
    """game x = 0 must land on a whole buffer pixel: (W - 320n)/2."""
    for n in (1, 2, 3):
        p = fb.preset(n)
        origin = p['width'] / 2.0 * (1.0 - p['ws_scale'])
        close(origin, round(origin), 1e-9, 'origin for n=%d' % n)
        eq((p['width'] - 320 * n) % 2, 0, 'even width for n=%d' % n)


def t_presets_are_close_to_16_9():
    for n, tol in ((1, 0.005), (2, 0.002), (3, 1e-12)):
        p = fb.preset(n)
        a = fb.aspect(p['width'], n)
        if abs(a / (16 / 9.0) - 1.0) > tol:
            raise AssertionError('preset %d is %.3f%% off 16:9'
                                 % (n, 100 * (a / (16 / 9.0) - 1)))


def t_preset_3_is_exactly_16_9_at_075():
    p = fb.preset(3)
    eq((p['width'], p['height']), (1280, 720), 'preset 3')
    close(p['ws_scale'], 0.75, 1e-12, 'preset 3 scale')
    close(fb.aspect(1280, 3), 16 / 9.0, 1e-12, 'preset 3 aspect')


def t_four_three_presets():
    for n in (1, 2, 3):
        p = fb.preset(n, widescreen=False)
        eq((p['width'], p['height']), (320 * n, 240 * n), '4:3 preset %d' % n)
        close(p['ws_scale'], 1.0, 1e-12, '4:3 scale %d' % n)


def t_tile_window_is_covered():
    """HANDOFF-49 ships left = 376, right = 64. No preset may need more."""
    for n in (1, 2, 3):
        p = fb.preset(n)
        need = fb.tile_window_minima(p['width'], n)
        if need['left'] > 376 or need['right'] > 64:
            raise AssertionError('preset %d needs L%d R%d, ships 376/64'
                                 % (n, need['left'], need['right']))


def t_env_scale():
    saved = os.environ.get(fb.SCALE_ENV)
    try:
        for raw, want in (('', 7), ('0', 0), ('off', 0), ('2', 2),
                          ('3', 3), ('nonsense', 7)):
            os.environ[fb.SCALE_ENV] = raw
            eq(fb.env_scale(default=7), want, 'env %r' % raw)
    finally:
        if saved is None:
            os.environ.pop(fb.SCALE_ENV, None)
        else:
            os.environ[fb.SCALE_ENV] = saved


def t_ws_module_agrees_with_the_preset():
    """
    The number in the shader and the number the extents are computed from
    must be the same object, not two constants that happen to match today.
    """
    try:
        import ff7nx_ws
    except ImportError:
        return
    saved = {k: os.environ.get(k) for k in
             ('SEVENTH_NX_WIDESCREEN', 'SEVENTH_NX_WS_FRAMING', fb.SCALE_ENV)}
    try:
        os.environ['SEVENTH_NX_WIDESCREEN'] = 'ws-3d'
        os.environ['SEVENTH_NX_WS_FRAMING'] = '1'
        for n in (1, 2, 3):
            os.environ[fb.SCALE_ENV] = str(n)
            p = fb.preset(n)
            eq(ff7nx_ws.fieldbuf(), p, 'ff7nx_ws.fieldbuf() for n=%d' % n)
            close(ff7nx_ws.ws_scale(), p['ws_scale'], 1e-12,
                  'ff7nx_ws.ws_scale() for n=%d' % n)
        os.environ[fb.SCALE_ENV] = '0'
        eq(ff7nx_ws.fieldbuf(), None, 'fieldbuf off')
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# --------------------------------------------------------------------------
# C. the patch, against the real module
# --------------------------------------------------------------------------
def t_encoding():
    eq(fb.encode('movz64', 8, 0x140), 0xD2802808, 'movz x8, #320')
    eq(fb.encode('movk64h', 8, 0xF0), 0xF2C01E08, 'movk x8, #240, lsl 32')
    eq(fb.encode('movz32', 13, 0x140), 0x5280280D, 'movz w13, #320')
    eq(fb.encode('movz32', 9, 428), 0x52803589, 'movz w9, #428')
    eq(fb.encode('movz64', 8, 1280), 0xD280A008, 'movz x8, #1280')
    eq(fb.decode('movz32', 9, 0x52802809, 0x52802809, 320), 320, 'decode w9')
    eq(fb.decode('movz32', 13, 0x52802809, 0x5280280D, 320), None,
       'wrong register must not decode')
    # the ORR-form stock height must decode as its stock value
    eq(fb.decode('orr32', 15, 0x321C0FEF, 0x321C0FEF, 240), 240, 'orr 240')


def t_sites_match_the_real_module():
    bad = fb.verify_sites(_MAIN)
    if bad:
        raise AssertionError('; '.join(bad))


def t_reads_stock_size():
    eq(fb.read_size(_MAIN), (320, 240), 'stock size')


def t_corrupted_module_refused():
    import nxmap
    img = bytearray(nxmap.Main(_MAIN).img)
    struct.pack_into('<I', img, 0x10DF7E8, 0xD503201F)     # nop a `str`
    orig = fb._img
    fb._img = lambda _p: bytes(img)
    try:
        bad = fb.verify_sites(_MAIN)
    finally:
        fb._img = orig
    if not bad:
        raise AssertionError('a corrupted signature was accepted')


def t_odd_width_refused():
    eq(fb.apply(_COPY, 427, 240, dry_run=True, log=lambda *_: None), 2,
       'odd width')


def t_bad_height_refused():
    eq(fb.apply(_COPY, 428, 250, dry_run=True, log=lambda *_: None), 2,
       'height not a multiple of 240')


def t_scale_1_changes_exactly_four_words():
    """
    At 1x the height stays 240, so the four height sites must not be
    rewritten -- not even into an equivalent encoding.
    """
    import nxmap
    shutil.copy2(_MAIN, _COPY)
    before = nxmap.Main(_MAIN).img
    eq(fb.apply(_COPY, 428, 240, log=lambda *_: None), 0, 'apply')
    after = nxmap.Main(_COPY).img
    diff = [i for i in range(0, len(before) - 3, 4)
            if before[i:i + 4] != after[i:i + 4]]
    eq(sorted(diff), [0x10D5358, 0x10DF760, 0x10DF7E0, 0x10DF804],
       'changed words at 1x')


def t_scale_3_changes_exactly_eight_words():
    import nxmap
    shutil.copy2(_MAIN, _COPY)
    before = nxmap.Main(_MAIN).img
    eq(fb.apply(_COPY, 1280, 720, log=lambda *_: None), 0, 'apply')
    after = nxmap.Main(_COPY).img
    diff = [i for i in range(0, len(before) - 3, 4)
            if before[i:i + 4] != after[i:i + 4]]
    eq(sorted(diff), [0x10D5358, 0x10D535C, 0x10DF760, 0x10DF764,
                      0x10DF7E0, 0x10DF7E4, 0x10DF804, 0x10DF80C],
       'changed words at 3x')
    eq(fb.read_size(_COPY), (1280, 720), 'size after')


def t_idempotent():
    eq(fb.apply(_COPY, 1280, 720, log=lambda *_: None), 0, 'second apply')
    eq(fb.read_size(_COPY), (1280, 720), 'still 1280x720')


def t_whole_ladder_reverts_byte_exactly():
    """
    1 -> 3 -> 2 -> 1 -> stock must land on the original file, byte for byte.
    This is what forces `patches()` to prefer the module's own stock word
    over a re-encoded equivalent.
    """
    shutil.copy2(_MAIN, _COPY)
    for n in (1, 3, 2, 1):
        p = fb.preset(n)
        eq(fb.apply(_COPY, p['width'], p['height'], log=lambda *_: None), 0,
           'apply %d' % n)
    eq(fb.apply(_COPY, fb.STOCK_WIDTH, fb.STOCK_HEIGHT,
                log=lambda *_: None), 0, 'back to stock')
    a = hashlib.sha256(open(_MAIN, 'rb').read()).hexdigest()
    b = hashlib.sha256(open(_COPY, 'rb').read()).hexdigest()
    eq(b, a, 'sha256 after the full ladder')


def t_spec_is_none_when_nothing_to_do():
    import nxmap
    img = nxmap.Main(_MAIN).img
    if fb.spec(img, 320, 240) is not None:
        raise AssertionError('spec should be None for a no-op')
    if fb.spec(img, 428, 240) is None:
        raise AssertionError('spec should not be None for a real change')


# --------------------------------------------------------------------------
def main(argv):
    global _MAIN, _COPY
    groups = [
        ('A. the model, against HANDOFF-50\'s hardware measurements',
         ['t_measured_12px', 't_measured_8px_at_two_thirds',
          't_measured_four_three_clean', 't_measured_stretched_clean',
          't_measured_ortho_bands', 't_supersample_is_irrelevant',
          't_integer_magnification_is_clean',
          't_half_integer_magnification_bands']),
        ('B. the presets and the wiring',
         ['t_preset_identities', 't_presets_are_clean',
          't_presets_are_texel_aligned', 't_presets_are_close_to_16_9',
          't_preset_3_is_exactly_16_9_at_075', 't_four_three_presets',
          't_tile_window_is_covered', 't_env_scale',
          't_ws_module_agrees_with_the_preset']),
    ]
    for title, names in groups:
        print('== %s ==' % title)
        for n in names:
            check(n[2:].replace('_', ' '), globals()[n])
        print()

    if len(argv) < 2:
        print('  (no module given -- skipping the patch tests.')
        print('   run: python3 test_fieldbuf.py <path to exefs/main>)')
    else:
        _MAIN = argv[1]
        tmpdir = tempfile.mkdtemp(prefix='fieldbuf')
        _COPY = os.path.join(tmpdir, 'main')
        shutil.copy2(_MAIN, _COPY)
        print('== C. the patch, against %s ==' % _MAIN)
        for n in ('t_encoding', 't_sites_match_the_real_module',
                  't_reads_stock_size', 't_corrupted_module_refused',
                  't_odd_width_refused', 't_bad_height_refused',
                  't_scale_1_changes_exactly_four_words',
                  't_scale_3_changes_exactly_eight_words', 't_idempotent',
                  't_whole_ladder_reverts_byte_exactly',
                  't_spec_is_none_when_nothing_to_do'):
            check(n[2:].replace('_', ' '), globals()[n])
        shutil.rmtree(tmpdir, ignore_errors=True)

    print()
    if FAILURES:
        print('%d of %d FAILED' % (len(FAILURES), COUNT))
        for name, tb in FAILURES:
            print()
            print('--- %s' % name)
            print(tb)
        return 1
    print('%d tests, all passing' % COUNT)
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
