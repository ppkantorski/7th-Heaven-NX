#!/usr/bin/env python3
"""
tests/test_voice_normalize.py -- Echo-S dialogue loudness.

The measurement that motivates the whole thing, taken over 160 random clips
of the shipped set:

    peak      min -12.0   median  -2.1   max  -0.0 dBFS    spread 12.0 dB
    loudness  min -28.4   median -16.1   max  -9.2 LUFS    spread 19.1 dB

The second row is the one you hear, and equalising the FIRST one leaves
16.4 dB of it standing. So levelling is by EBU R128 loudness, not by peak.

BUT NOT BROADBAND LOUDNESS. By build 348 the shipped set measured flat to
2.2 dB and still sounded uneven, because energy below 300 Hz counts fully
towards the loudness number and carries no speech. On those same flat files
the SPEECH BAND was still 13.4 dB apart, and two lines of one conversation
differed by 12 dB in it. So the gain is measured through 300 Hz - 4 kHz and
the target is the level of Jessie's "My hero!" in that band.

Nothing is filtered on the way out: the clip is encoded whole and gets one
flat gain. The band only decides what that number is.

The end-to-end tests need ffmpeg and the mod's clips; they skip without
either, so this file stays runnable on a bare checkout.

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
import glob
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import voice_ogg as V                                          # noqa: E402

CLIPS = os.path.join(ROOT, 'cache', 'Echo-s', 'VO', 'Voice')
REFERENCE = os.path.join(CLIPS, 'nmkin_3', '6.ogg')    # Jessie, "My hero!"


def _reload(value):
    """Re-import the module under one setting and hand it back."""
    import importlib
    if value is None:
        os.environ.pop(V.NORMALIZE_ENV, None)
    else:
        os.environ[V.NORMALIZE_ENV] = value
    sys.modules.pop('voice_ogg', None)
    return importlib.import_module('voice_ogg')


def _restore():
    _reload(None)


def _ffmpeg():
    for name in (os.environ.get('SEVENTH_NX_FFMPEG'), 'ffmpeg'):
        if not name:
            continue
        try:
            subprocess.run([name, '-version'], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=True)
            return name
        except Exception:                                      # noqa: BLE001
            continue
    return None


def _measure(path, tool):
    """(peak dBFS, integrated LUFS) of a finished clip."""
    out = subprocess.run(
        [tool, '-hide_banner', '-nostdin', '-i', path, '-af',
         'volumedetect,loudnorm=print_format=json', '-f', 'null', '-'],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stderr
    peak = re.search(r'max_volume:\s*(-?[\d.]+) dB', out)
    loud = re.search(r'"input_i"\s*:\s*"(-?[\d.]+)"', out)
    assert peak and loud, out[-400:]
    return float(peak.group(1)), float(loud.group(1))


# ------------------------------------------------------------ the setting
def test_the_setting_parses_and_refuses_nonsense():
    try:
        for value, mode, target in (
                ('speech:-16.2', 'speech', -16.2),
                ('loudness:-15.3', 'loudness', -15.3),
                ('loudness:-13', 'loudness', -13.0),
                ('peak:-1.1', 'peak', -1.1),
                ('off', 'off', 0.0),
                ('OFF', 'off', 0.0),
                ('none', 'off', 0.0),
                ('', 'off', 0.0)):
            module = _reload(value)
            assert module.NORMALIZE_MODE == mode, value
            assert abs(module.NORMALIZE_TARGET - target) < 1e-9, value
        for bad in ('rms:-15', 'loudness', 'loudness:zero', 'loudness:5',
                    'loudness:-500', 'peak:3'):
            try:
                _reload(bad)
            except Exception as exc:                           # noqa: BLE001
                assert 'SEVENTH_NX_VOICE_NORMALIZE' in str(exc), bad
                continue
            raise AssertionError('%r was accepted' % bad)
    finally:
        _restore()


def test_the_setting_is_in_the_cache_key():
    """
    Change it and every clip re-encodes; change it back and none do. That is
    the whole contract of this cache, and the reason the level is safe to
    experiment with: every level you have already built is still there.
    """
    try:
        recipes = {}
        for value in ('loudness:-15.3', 'loudness:-13', 'peak:-1.1', 'off'):
            recipes[value] = _reload(value).RECIPE
        assert len(set(recipes.values())) == len(recipes), recipes
        assert _reload('loudness:-15.3').RECIPE == recipes['loudness:-15.3']
        assert 'norm=off' in recipes['off']
    finally:
        _restore()


def test_every_choice_the_gui_offers_is_a_valid_setting():
    """The dialog and the encoder must not be able to disagree."""
    import ast
    source = open(os.path.join(ROOT, '7th_heaven_nx.py'),
                  encoding='utf-8').read()
    found = re.search(r'VOICE_NORM_CHOICES = (\[.*?\])\nVOICE_NORM_LABELS',
                      source, re.S)
    assert found, 'the settings dialog has no voice choices'
    choices = ast.literal_eval(found.group(1))
    assert len(choices) >= 3
    try:
        for value, label in choices:
            module = _reload(value)
            assert module.NORMALIZE_MODE in ('off', 'speech', 'loudness',
                                             'peak'), value
            assert label.strip()
        values = [v for v, _l in choices]
        assert V.NORMALIZE_DEFAULT in values, 'the default is not offered'
        assert len(set(values)) == len(values)
    finally:
        _restore()


def test_it_cannot_invalidate_an_archive_cache():
    """It writes loose .ogg under music_ogg and nothing else, so it has no
    business in any archive fingerprint -- the same rule the bitrate follows."""
    try:
        import build
    except ImportError:
        return
    assert V.NORMALIZE_ENV in build.MAIN_ONLY_ENV
    assert V.NORMALIZE_ENV in build.ARCHIVE_NEUTRAL_ENV


# --------------------------------------------------------- the measurement
def test_the_reference_clip_is_the_level_the_default_targets():
    """
    The target is not a taste judgement dressed as a number: it is what
    Jessie's "My hero!" measures, in the band it is measured in. So the
    default must leave that clip alone -- it is the one line confirmed by ear
    to be at the right level, and every other line is moved to meet it.
    """
    tool = _ffmpeg()
    if not tool or not os.path.isfile(REFERENCE):
        return
    try:
        for value in (V.NORMALIZE_DEFAULT, 'loudness:-15.3'):
            module = _reload(value)
            gain, why = module._measure(tool, REFERENCE)
            assert why is None, why
            assert abs(gain) < 0.5, (
                '%s: the reference clip needs %.2f dB' % (value, gain))
    finally:
        _restore()


# ------------------------------------------------- the band, BUILD 501
def test_the_default_measures_the_speech_band():
    assert V.NORMALIZE_DEFAULT.startswith('speech:')
    assert V.NORMALIZE_MODE == 'speech'
    assert V.SPEECH_BAND_LOW == 300 and V.SPEECH_BAND_HIGH == 4000
    assert 'highpass=f=300' in V.SPEECH_FILTER
    assert 'lowpass=f=4000' in V.SPEECH_FILTER


def test_the_band_is_in_the_cache_key_and_the_modes_are_distinct():
    """`speech:-16.2` and `loudness:-16.2` are different encodes and must
    never be served each other's cached clips."""
    try:
        a = _reload('speech:-16.2').RECIPE
        b = _reload('loudness:-16.2').RECIPE
        assert a != b, (a, b)
        assert '300-4000' in a
    finally:
        _restore()


def test_a_bass_heavy_clip_gets_a_different_gain_than_broadband_would():
    """
    THE WHOLE MECHANISM, on a synthesised version of the case that caused
    this: a clip whose energy sits under 300 Hz. Broadband levelling reads it
    as loud and turns it DOWN; speech-band levelling sees that its speech is
    faint and lifts it. If these two ever agree, the filter is not being
    applied.
    """
    tool = _ffmpeg()
    if not tool:
        return
    import tempfile
    out = tempfile.mkdtemp(prefix='voiceband-')
    path = os.path.join(out, 'boomy.ogg')
    try:
        # A quiet 1 kHz tone (the "speech") under a loud 200 Hz boom. 200 Hz
        # rather than 90: K-weighting already discounts the deep end, and the
        # real clips carry theirs at 120-300 Hz, which it barely touches.
        # That is exactly why they read loud and sound faint.
        subprocess.run(
            [tool, '-hide_banner', '-nostdin', '-loglevel', 'error', '-y',
             '-f', 'lavfi', '-i', 'sine=frequency=1000:r=48000:duration=4',
             '-f', 'lavfi', '-i', 'sine=frequency=200:r=48000:duration=4',
             '-filter_complex',
             '[0:a]volume=-30dB[a];[1:a]volume=-6dB[b];[a][b]amix=normalize=0',
             '-c:a', 'libvorbis', path], check=True)
        speech_gain = _reload('speech:-16.2')._measure(tool, path)[0]
        broad_gain = _reload('loudness:-16.2')._measure(tool, path)[0]
        # the band asks for far more gain, because it can see that the
        # SPEECH is faint while the clip as a whole is not
        assert speech_gain > broad_gain + 6.0, (speech_gain, broad_gain)
    finally:
        _restore()
        import shutil
        shutil.rmtree(out, ignore_errors=True)


def test_the_settle_probe_measures_the_same_band_it_targeted():
    """
    A probe that measured BROADBAND on a speech-band-targeted encode would
    have the settle loop converge -- on the wrong answer, silently. The two
    measurements have to be of the same thing.
    """
    tool = _ffmpeg()
    if not tool or not os.path.isfile(REFERENCE):
        return
    try:
        module = _reload('speech:-16.2')
        chain = module._filter_chain(0.0)
        through_band = module._probe_chain(tool, REFERENCE, chain)
        assert through_band is not None
        # it must agree with measuring the source through the band, not with
        # the broadband number (which for this clip is ~1 dB higher)
        assert abs(through_band - (-16.2)) < 1.0, through_band
    finally:
        _restore()


def test_a_silent_clip_is_left_alone():
    """Echo-S ships silent placeholders. Lifting a noise floor by 20 dB is
    the one thing a normaliser must never do."""
    tool = _ffmpeg()
    if not tool:
        return
    import tempfile
    handle, path = tempfile.mkstemp(suffix='.ogg')
    os.close(handle)
    try:
        subprocess.run(
            [tool, '-hide_banner', '-nostdin', '-loglevel', 'error', '-y',
             '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo', '-t', '1',
             path], check=True)
        for value in ('loudness:-15.3', 'peak:-1.1'):
            module = _reload(value)
            gain, why = module._measure(tool, path)
            assert gain == 0.0, (value, gain)
            assert why == 'silence', (value, why)
    finally:
        _restore()
        os.unlink(path)


def test_an_unmeasurable_clip_is_shipped_rather_than_dropped():
    """A line at the wrong volume beats a line that is missing."""
    tool = _ffmpeg()
    if not tool:
        return
    try:
        module = _reload('loudness:-15.3')
        gain, why = module._measure(tool, os.path.join(ROOT, 'no-such.ogg'))
        assert gain == 0.0 and why, (gain, why)
    finally:
        _restore()


# ------------------------------------------------------------- end to end
def _encode_sample(setting, count=12):
    tool = _ffmpeg()
    clips = sorted(glob.glob(os.path.join(CLIPS, '*', '*.ogg')))
    if not tool or len(clips) < count:
        return None, None
    import tempfile
    module = _reload(setting)
    out = tempfile.mkdtemp(prefix='voicenorm-test-')
    made = []
    for i, source in enumerate(clips[::max(1, len(clips) // count)][:count]):
        target = os.path.join(out, '%03d.ogg' % i)
        module.encode(source, target)
        made.append((source, target))
    return tool, made


def test_normalising_collapses_the_spread_without_clipping():
    """
    The claim, on real clips: one gain per clip takes the loudness spread
    from ~19 dB to a couple, EVERY clip lands at the target -- not merely
    "unless its own peak forbids it", which is the V6 behaviour this replaced
    -- and nothing clips, because a positive gain is followed by a limiter.
    """
    import shutil
    tool, made = _encode_sample('loudness:-15.3')
    if not made:
        return
    try:
        levels = [_measure(target, tool) for _source, target in made]
        loud = sorted(level for _peak, level in levels)
        peaks = [peak for peak, _level in levels]
        assert max(peaks) <= -0.1, ('a clip clipped: %.2f dBFS' % max(peaks))
        assert loud[-1] <= -15.3 + 0.7, loud[-1]
        # V6 allowed 12 dB here because the peak cap kept a third of the set
        # short of the target. With the limiter there is no longer any reason
        # for a clip to land low, so the bar is where it should always have
        # been: if this regresses, the cap is back.
        assert loud[-1] - loud[0] < 3.0, (
            'spread is %.1f dB -- something is holding clips below target'
            % (loud[-1] - loud[0]))
        assert loud[0] >= -15.3 - 2.0, (
            'quietest clip landed at %.1f LUFS, %.1f dB short of target'
            % (loud[0], -15.3 - loud[0]))
    finally:
        _restore()
        shutil.rmtree(os.path.dirname(made[0][1]), ignore_errors=True)


# --------------------------------------------------------- the limiter
def test_the_limiter_is_only_on_the_path_that_can_clip():
    """
    A clip being turned DOWN cannot clip, so it must not be limited: running
    a limiter on it would be a second, unmeasured gain stage on a path that
    asked for one exact gain.
    """
    module = _reload('loudness:-15.3')
    try:
        up = module._filter_chain(6.0)
        down = module._filter_chain(-6.0)
        flat = module._filter_chain(0.0)

        assert 'alimiter' in up, up
        assert 'alimiter' not in down, down
        assert 'alimiter' not in flat, flat

        # and the gain must come first, or the limiter has nothing to catch
        assert up.index('volume=') < up.index('alimiter'), up
        # ... and the lead-in silence last, or it is inside the measurement
        assert up.index('alimiter') < up.index('adelay='), up
        assert down.index('volume=') < down.index('adelay='), down
        assert flat.startswith('adelay='), flat

        limit = float(re.search(r'alimiter=limit=([\d.]+)', up).group(1))
        expected = 10.0 ** (module.LIMITER_CEILING_DB / 20.0)
        assert abs(limit - expected) < 1e-6, (limit, expected)
        # -1.0 and -1.5 dBFS both left true peaks above the -1.0 dBTP ceiling
        # once the codec resampled; -2.0 is the measured headroom that holds.
        assert module.LIMITER_CEILING_DB <= -2.0, module.LIMITER_CEILING_DB
        assert module.LIMITER_CEILING_DB <= module.TRUE_PEAK_CEILING
    finally:
        _restore()


def test_the_settle_loop_only_runs_where_it_can_help():
    """
    It must be inert in peak mode (which measures the thing it sets), inert
    when nothing is being gained up (no limiter, nothing to take gain back),
    and inert when the clip could not be measured -- because in all three the
    probe would be a cost with no possible answer.
    """
    module = _reload('loudness:-15.3')
    try:
        calls = []

        def _no_probe(ffmpeg, source, chain):
            calls.append(chain)
            return None

        module._probe_chain = _no_probe
        assert module._settled_gain('ffmpeg', 'x.ogg', -3.0, -12.3) == -3.0
        assert module._settled_gain('ffmpeg', 'x.ogg', 0.0, -15.3) == 0.0
        assert module._settled_gain('ffmpeg', 'x.ogg', 4.0, None) == 4.0
        assert calls == [], calls
        # and a probe that fails leaves the gain exactly as it was, so this
        # can never be worse than not having it
        assert module._settled_gain('ffmpeg', 'x.ogg', 4.0, -19.3) == 4.0
        assert len(calls) == 1, calls
    finally:
        _restore()


def test_the_settle_loop_solves_rather_than_nudges():
    """
    The correction is a secant step off the slope the first probe reveals,
    and it must stop: on reaching the target, on a clip that gives back no
    loudness for more gain, and on the step budget.
    """
    module = _reload('loudness:-15.3')
    try:
        # a clip where only 60% of each dB survives the limiter, like
        # fship_25/127a measured at 0.61
        seen = []

        def _sixty_percent(ffmpeg, source, gain):
            seen.append(gain)
            return -21.8 + 0.6 * gain

        module._probe_chain = lambda f, s, chain: _sixty_percent(
            f, s, float(re.search(r'volume=(-?[\d.]+)dB', chain).group(1)))
        settled = module._settled_gain('ffmpeg', 'x.ogg', 6.5, -21.8)
        assert abs(-21.8 + 0.6 * settled - -15.3) <= 0.3, (settled, seen)
        assert len(seen) <= 1 + module.NORMALIZE_SETTLE_STEPS, seen

        # a clip that is pure transient: more gain, no more loudness. It must
        # not keep pushing -- that is just compression nobody asked for.
        flat = []

        def _flat(ffmpeg, source, chain):
            flat.append(chain)
            return -20.0

        module._probe_chain = _flat
        settled = module._settled_gain('ffmpeg', 'x.ogg', 4.7, -24.7)
        assert len(flat) <= 2, flat
        assert settled <= 4.7 + module.MAX_GAIN_DB

        # and it never exceeds the gain ceiling, however far short it lands
        module._probe_chain = lambda f, s, chain: -40.0
        assert module._settled_gain(
            'ffmpeg', 'x.ogg', 19.0, -34.3) <= module.MAX_GAIN_DB
    finally:
        _restore()


def test_a_clip_the_old_cap_held_down_now_reaches_the_target():
    """
    The bug the user heard, reproduced as a test: a quiet clip with a peak
    already near full scale. Under the peak cap it could only be lifted until
    its peak hit the ceiling, landing far short of the target no matter how
    quiet it actually was. It must now arrive at the target.
    """
    import shutil
    import tempfile
    tool = _ffmpeg()
    if not tool:
        return
    out = tempfile.mkdtemp(prefix='voicenorm-cap-')
    source = os.path.join(out, 'src.ogg')
    try:
        # A quiet tone -- about -30 LUFS -- with two full-scale ticks in it.
        # Integrated loudness barely notices the ticks; a peak meter sees
        # nothing else. That gap is exactly the class of clip that broke.
        subprocess.run(
            [tool, '-hide_banner', '-nostdin', '-loglevel', 'error', '-y',
             '-f', 'lavfi', '-i', 'sine=frequency=300:r=44100:duration=4',
             '-af', 'volume=-15dB,'
                    'volume=enable=\'between(t,1.0,1.002)\':volume=20,'
                    'alimiter=limit=0.999:level=disabled',
             '-ac', '2', source], check=True)
        before_peak, before_loud = _measure(source, tool)
        if before_peak > -0.5 and before_loud < -25.0:
            module = _reload('loudness:-15.3')
            target = os.path.join(out, 'out.ogg')
            module.encode(source, target)
            after_peak, after_loud = _measure(target, tool)
            # the cap would have allowed +1.1 dB at most, landing near -29
            assert after_loud > -19.0, (
                'landed at %.1f LUFS from %.1f -- the peak is still holding '
                'it down' % (after_loud, before_loud))
            assert after_peak <= -0.1, (
                'it clipped: %.2f dBFS' % after_peak)
    finally:
        _restore()
        shutil.rmtree(out, ignore_errors=True)


def test_off_changes_nothing_at_all():
    """The feature has to be genuinely inert when it is off, or 'off' is not
    a way back to what the mod shipped."""
    import shutil
    tool, made = _encode_sample('off', count=6)
    if not made:
        return
    try:
        for source, target in made:
            before = _measure(source, tool)
            after = _measure(target, tool)
            # the encode still resamples and pads, so allow the codec's own
            # drift -- what must not have happened is a gain
            assert abs(after[1] - before[1]) < 1.5, (source, before, after)
    finally:
        _restore()
        shutil.rmtree(os.path.dirname(made[0][1]), ignore_errors=True)


def _main():
    fails = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith('test_') or not callable(fn):
            continue
        try:
            fn()
            print('  ok    %s' % name)
        except AssertionError as exc:
            fails += 1
            print('  FAIL  %s: %s' % (name, exc))
        except Exception as exc:                               # noqa: BLE001
            fails += 1
            print('  ERROR %s: %s: %s' % (name, type(exc).__name__, exc))
    print('\n%s' % ('PASS' if not fails else '%d FAILURE(S)' % fails))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(_main())
