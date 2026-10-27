#!/usr/bin/env python3
"""
tests/test_movie_audio.py -- FFNx's two movie audio layers.

FFNx plays three things during a cutscene, not one (`src/movies.cpp`):

    the video's own audio stream, UNLESS movies/<NAME>.ogg exists
    movies/<NAME>.ogg          the replacement soundtrack
    movies/<NAME>_va.ogg       a voice layer, mixed on top at volume 3.0f

The Switch port has neither extra layer, so for a long time every file under
a mod's `movies/` folder was counted as "FFNx audio with nowhere to go" and
dropped. With Echo-S and Cosmo Memory installed that is 15 voice tracks --
every word spoken during an FMV -- and 64 remastered soundtracks.

They now go into the mp4's audio track at conversion time. These tests cover
the routing (which file is which layer), the level (measured per movie, not
FFNx's fixed 3.0f), and the end-to-end result on real audio.

The end-to-end tests need ffmpeg; they skip without it.

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
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import build                                                   # noqa: E402
import movies as M                                             # noqa: E402


# ---------------------------------------------------------------- routing
def test_the_two_layers_are_told_apart_by_name():
    assert M.movie_audio_kind('SOUTHMK_VA.ogg') == ('voice', 'southmk')
    assert M.movie_audio_kind('SOUTHMK.ogg') == ('music', 'southmk')
    assert M.movie_audio_kind('a/b/JENOVA_E_VA.ogg') == ('voice', 'jenova_e')
    assert M.movie_audio_kind('opening_va.ogg') == ('voice', 'opening')


def test_a_disabled_file_is_not_a_layer():
    """`_OPENING.ogg` and `_BIKE.ogg` are how these mods switch a file off in
    place. No movie's name begins with an underscore, so taking them would
    be taking a file the author had removed."""
    for name in ('_OPENING.ogg', '_opening.ogg', '_BIKE.ogg',
                 '_OPENING (2).ogg', '_VA.ogg'):
        assert M.movie_audio_kind(name) is None, name


def test_only_ogg_is_a_layer():
    for name in ('SOUTHMK.avi', 'SOUTHMK_VA.wav', 'SOUTHMK', ''):
        assert M.movie_audio_kind(name) is None, name


# The shape Echo-S and Cosmo Memory actually ship.
TREE = [
    'Common/movies/SOUTHMK_VA.ogg',
    'Common/movies/BIGLIGHT_VA.ogg',
    'Common/movies/_OPENING.avi',          # disabled video, not a layer
    'Common/lang-en/movies/ENDING2_VA.ogg',
    'FMV/movies/SOUTHMK.ogg',
    'FMV/movies/NORTHMK.ogg',
    'FMV/movies/_BIKE.ogg',                # disabled, must not be taken
    'FMV/lang-en/movies/ENDING2.ogg',
    'Base/sfx/6539.ogg',                   # still FFNx sound, still skipped
    'Base/Ambient/12.ogg',                 # ditto
]
MANIFEST = ('<ModFolder Folder="Common"/><ModFolder Folder="FMV"/>'
            '<ModFolder Folder="Base"/>')


def _plan():
    root = tempfile.mkdtemp()
    cache = os.path.join(root, 'cache')
    moddir = os.path.join(cache, 'TestMod')
    for rel in TREE:
        p = os.path.join(moddir, rel.replace('/', os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'wb').write(b'\0' * 16)
    with open(os.path.join(moddir, 'mod.xml'), 'w') as f:
        f.write('<?xml version="1.0"?>\n<ModInfo>\n<ID>t</ID><Name>T</Name>\n'
                + MANIFEST + '\n</ModInfo>\n')
    iro_path = os.path.join(root, 'TestMod.iro')
    open(iro_path, 'wb').write(b'')
    mod = build.Mod(iro_path, cache)
    mod._load_manifest()
    settings = mod.manifest.defaults() if mod.manifest else {}
    return build.build_plan([mod], {mod.filename: settings}, {},
                            lambda *a, **k: None)


def test_the_planner_collects_both_layers_and_still_skips_the_rest():
    plan = _plan()
    assert ('southmk', 'voice') in plan.movie_audio
    assert ('biglight', 'voice') in plan.movie_audio
    assert ('ending2', 'voice') in plan.movie_audio
    assert ('southmk', 'music') in plan.movie_audio
    assert ('northmk', 'music') in plan.movie_audio
    assert ('ending2', 'music') in plan.movie_audio
    # the disabled file is not a layer, and is not silently taken as one
    assert ('bike', 'music') not in plan.movie_audio
    assert ('_bike', 'music') not in plan.movie_audio
    # ... it is the ONLY thing under movies/ still counted as skipped
    assert plan.skipped_ffnx_audio.get('movies', 0) == 1
    # and nothing else moved: the other FFNx audio directories route where
    # they did before, which is not here
    assert len(plan.sfx) == 1 and plan.sfx[0][0].endswith('6539.ogg')
    assert len(plan.ambient) == 1
    # a layer is audio, not a movie: none of them reached the video list
    assert not any(s in plan.movies for s in
                   ('southmk', 'northmk', 'ending2', 'biglight'))
    # nor the music_ogg stream, which is where a wrong turn here would put
    # 79 files on top of the soundtrack
    assert not plan.music


# ------------------------------------------------------------- the level
def test_the_voice_is_placed_against_the_bed_not_at_a_fixed_gain():
    """
    FFNx multiplies every voice track by 3.0. Measured over Echo-S's own
    files the voice varies from -11.4 to -17.4 LUFS and the beds under them
    from -15.0 to -22.4, so one constant puts the voice 6 dB further forward
    in one scene than another for no reason but the recording.
    """
    assert M.voice_gain_db(-15.0, -13.0, balance=6.0) == 4.0
    assert M.voice_gain_db(-22.4, -17.4, balance=6.0) == 1.0
    # a quiet voice under a loud bed is lifted, the reverse is lowered
    assert M.voice_gain_db(-10.0, -20.0, balance=6.0) > 0
    assert M.voice_gain_db(-30.0, -10.0, balance=6.0) < 0


def test_an_unmeasurable_track_is_left_exactly_as_recorded():
    """A guessed gain on a track nothing could measure is worse than none."""
    assert M.voice_gain_db(None, -13.0) == 0.0
    assert M.voice_gain_db(-15.0, None) == 0.0
    assert M.voice_gain_db(None, None) == 0.0


def test_the_gain_is_bounded():
    assert M.voice_gain_db(-5.0, -50.0) == M.VOICE_MAX_GAIN_DB
    assert M.voice_gain_db(-50.0, -5.0) == -M.VOICE_MAX_GAIN_DB


def test_the_balance_is_a_setting_that_refuses_nonsense():
    before = os.environ.get(M.VOICE_BALANCE_ENV)
    try:
        os.environ[M.VOICE_BALANCE_ENV] = '9'
        assert M.voice_balance_lu() == 9.0
        os.environ[M.VOICE_BALANCE_ENV] = ''
        assert M.voice_balance_lu() == M.VOICE_BALANCE_LU
        for bad in ('loud', '40', '-40'):
            os.environ[M.VOICE_BALANCE_ENV] = bad
            try:
                M.voice_balance_lu()
            except ValueError:
                pass
            else:
                raise AssertionError('%r was accepted' % bad)
    finally:
        if before is None:
            os.environ.pop(M.VOICE_BALANCE_ENV, None)
        else:
            os.environ[M.VOICE_BALANCE_ENV] = before


# ------------------------------------------------------------- end to end
def _have_ffmpeg():
    return M.have_ffmpeg()


def _measure(path):
    out = subprocess.run(
        [M._tool('ffmpeg'), '-hide_banner', '-nostdin', '-i', path,
         '-af', 'volumedetect,loudnorm=print_format=json', '-f', 'null', '-'],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stderr
    grab = lambda p: (lambda m: float(m.group(1)) if m else None)(
        re.search(p, out))
    return (grab(r'max_volume:\s*(-?[\d.]+) dB'),
            grab(r'"input_i"\s*:\s*"(-?[\d.]+)"'),
            grab(r'"input_tp"\s*:\s*"(-?[\d.]+)"'))


def _make_sources(out):
    """A 6s silent-picture movie with a bed, and a voice track over it."""
    tool = M._tool('ffmpeg')
    video = os.path.join(out, 'movie.mp4')
    subprocess.run(
        [tool, '-hide_banner', '-loglevel', 'error', '-y',
         '-f', 'lavfi', '-i', 'testsrc=size=320x240:rate=15:duration=6',
         '-f', 'lavfi', '-i', 'sine=frequency=200:r=44100:duration=6',
         '-af', 'volume=-14dB', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
         '-c:a', 'aac', '-shortest', video], check=True)
    voice = os.path.join(out, 'movie_va.ogg')
    subprocess.run(
        [tool, '-hide_banner', '-loglevel', 'error', '-y',
         '-f', 'lavfi', '-i', 'sine=frequency=900:r=48000:duration=6',
         '-af', 'volume=-26dB', '-c:a', 'libvorbis', voice], check=True)
    music = os.path.join(out, 'movie.ogg')
    subprocess.run(
        [tool, '-hide_banner', '-loglevel', 'error', '-y',
         '-f', 'lavfi', '-i', 'sine=frequency=400:r=48000:duration=6',
         '-af', 'volume=-8dB', '-c:a', 'libvorbis', music], check=True)
    return video, music, voice


def test_the_voice_reaches_the_mp4_and_the_sound_stays_with_the_picture():
    if not _have_ffmpeg():
        return
    out = tempfile.mkdtemp(prefix='movieaudio-')
    video, _music, voice = _make_sources(out)
    plain = os.path.join(out, 'plain.mp4')
    mixed = os.path.join(out, 'mixed.mp4')
    a = M.convert(video, plain, fit='native', colour='off')
    b = M.convert(video, mixed, voice=voice, fit='native', colour='off')

    assert a['voice_gain'] is None
    assert b['voice_gain'] is not None
    # the bed is -14 dB of tone and the voice -26, so the voice has to come
    # UP to sit above it -- roughly the 12 dB of difference plus the balance
    assert b['voice_gain'] > 6.0, b['voice_gain']

    # the mix is louder than the bed alone, and it does not clip
    assert _measure(mixed)[1] > _measure(plain)[1] + 1.0
    assert _measure(mixed)[2] <= M.MOVIE_LIMITER_DB + 0.3, _measure(mixed)

    # AND THE SOUND STILL AGREES WITH THE PICTURE. convert() raises if it
    # does not, so this is belt and braces on the number it checks.
    assert abs(b['out']['adur'] - b['out']['vdur']) < 0.2, b['out']


def test_a_soundtrack_replaces_the_movies_own_audio():
    """FFNx opens the video WITHOUT its audio stream when <NAME>.ogg exists,
    so this must be a replacement and not a second bed."""
    if not _have_ffmpeg():
        return
    out = tempfile.mkdtemp(prefix='movieaudio-')
    video, music, _voice = _make_sources(out)
    plain = os.path.join(out, 'plain.mp4')
    swapped = os.path.join(out, 'swapped.mp4')
    M.convert(video, plain, fit='native', colour='off')
    M.convert(video, swapped, music=music, fit='native', colour='off')
    # the bed is a 200 Hz tone at -14 dB and the replacement a 400 Hz tone at
    # -8; if the two had been summed the result would be louder than either
    # and a SUM of the two would be louder still, so the level pins which
    # of the two happened
    before, after = _measure(plain)[1], _measure(swapped)[1]
    assert after > before + 3.0, (before, after)
    assert after < -5.0, ('%.1f LUFS is the two summed, not swapped' % after)


def test_a_voice_track_longer_than_its_movie_is_cut_at_the_end():
    """`ff7_stop_movie` stops the voice layer, so a track that outlasts the
    picture is simply truncated. Echo-S has several: JAIROFLY_VA is 54.7s
    against a 41.2s movie."""
    if not _have_ffmpeg():
        return
    out = tempfile.mkdtemp(prefix='movieaudio-')
    video, _music, _voice = _make_sources(out)
    long_voice = os.path.join(out, 'long_va.ogg')
    subprocess.run(
        [M._tool('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y',
         '-f', 'lavfi', '-i', 'sine=frequency=900:r=48000:duration=20',
         '-af', 'volume=-26dB', '-c:a', 'libvorbis', long_voice], check=True)
    dest = os.path.join(out, 'cut.mp4')
    r = M.convert(video, dest, voice=long_voice, fit='native', colour='off')
    assert r['out']['vdur'] < 7.0, r['out']
    assert abs(r['out']['adur'] - r['out']['vdur']) < 0.2, r['out']


def test_a_missing_layer_is_simply_not_used():
    if not _have_ffmpeg():
        return
    out = tempfile.mkdtemp(prefix='movieaudio-')
    video, _music, _voice = _make_sources(out)
    dest = os.path.join(out, 'none.mp4')
    r = M.convert(video, dest,
                  music=os.path.join(out, 'nope.ogg'),
                  voice=os.path.join(out, 'nope_va.ogg'),
                  fit='native', colour='off')
    assert r['music'] is None and r['voice'] is None
    assert r['out']['adur'], 'the movie lost its own audio'


class _Dump:
    def __init__(self, workingdir):
        self.workingdir = workingdir
        self.exe = self.nso = None


def test_emplacement_muxes_a_layer_onto_a_movie_no_mod_replaced():
    """
    The wiring, end to end, on the case that motivated this: Echo-S ships
    `SOUTHMK_VA.ogg` and NO southmk video, so the movie has to be pulled out
    of the dump and rebuilt purely because its sound changed. Before this it
    was not in the work set at all.
    """
    if not _have_ffmpeg():
        return
    out = tempfile.mkdtemp(prefix='movieemplace-')
    video, _music, voice = _make_sources(out)

    wd = os.path.join(out, 'workingdir')
    os.makedirs(os.path.join(wd, 'data', 'movies'))
    vanilla = os.path.join(wd, 'data', 'movies', 'southmk.mp4')
    import shutil
    shutil.copyfile(video, vanilla)

    class Plan:
        pass
    plan = Plan()
    plan.movies = {}                                   # nobody replaced it
    plan.movie_audio = {('southmk', 'voice'): (voice, None)}
    plan.opening_fps = plan.opening_dims = None

    romfs = os.path.join(out, 'romfs')
    sdout = os.path.join(out, 'sdout')
    os.makedirs(sdout)
    lines, produced = [], []
    real_30 = build.movie_30fps
    build.movie_30fps = lambda: False       # not the frame-rate pass
    try:
        build._emplace_movies(plan, romfs, sdout, _Dump(wd),
                              lines.append, lambda *a: None, produced)
    finally:
        build.movie_30fps = real_30

    built = os.path.join(romfs, 'data', 'movies', 'southmk.mp4')
    assert os.path.exists(built), '\n'.join(lines)
    # it was rebuilt for its SOUND, with the voice in it and louder than the
    # file it came from
    assert _measure(built)[1] > _measure(vanilla)[1] + 1.0, '\n'.join(lines)
    assert any('voice' in line for line in lines), '\n'.join(lines)
    assert any('movie audio' in line for line in lines), '\n'.join(lines)


def test_emplacement_says_when_a_layer_names_no_movie():
    """A soundtrack for a cutscene that does not exist must be reported, not
    dropped in silence -- that is the failure mode this whole change is
    fixing, one directory along."""
    if not _have_ffmpeg():
        return
    out = tempfile.mkdtemp(prefix='movieemplace-')
    _video, music, _voice = _make_sources(out)
    wd = os.path.join(out, 'workingdir')
    os.makedirs(os.path.join(wd, 'data', 'movies'))

    class Plan:
        pass
    plan = Plan()
    plan.movies = {}
    plan.movie_audio = {('nosuchfilm', 'music'): (music, None)}
    plan.opening_fps = plan.opening_dims = None
    lines = []
    real_30 = build.movie_30fps
    build.movie_30fps = lambda: False
    try:
        build._emplace_movies(plan, os.path.join(out, 'romfs2'),
                              os.path.join(out, 'sdout2'), _Dump(wd),
                              lines.append, lambda *a: None, [])
    finally:
        build.movie_30fps = real_30
    assert any('nosuchfilm' in line for line in lines), '\n'.join(lines)


def test_a_movie_with_no_layer_keeps_the_cache_key_it_had():
    """
    THE WHOLE FMV SET MUST NOT RE-ENCODE. A movie nobody shipped audio for
    is built by the same command from the same inputs and comes out byte for
    byte what it was, so its key has to be the string it was before this
    feature existed -- which means the audio part of the key is EMPTY, not
    'none' or '-|-|0'. Getting this wrong costs an hour of encoding to
    reproduce files that are already in the cache.
    """
    assert M.audio_key_part() == ''
    assert M.audio_key_part(None, None) == ''
    assert M.CONVERTER_VERSION == 'MOVIECONV-V2'


def test_a_movie_that_gained_a_layer_gets_a_new_key():
    if not _have_ffmpeg():
        return
    out = tempfile.mkdtemp(prefix='moviekey-')
    _video, music, voice = _make_sources(out)
    with_voice = M.audio_key_part(None, voice)
    with_music = M.audio_key_part(music, None)
    both = M.audio_key_part(music, voice)
    assert len({with_voice, with_music, both, ''}) == 4
    # and the balance is in it, because it changes what comes out
    assert M.audio_key_part(None, voice, balance=6.0) \
        != M.audio_key_part(None, voice, balance=9.0)
    # ... but only for a voice: it cannot change a soundtrack-only movie
    assert M.audio_key_part(music, None, balance=6.0) \
        == M.audio_key_part(music, None, balance=9.0)


def _main():
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith('test_') and callable(fn):
            try:
                fn()
                print('  ok    ' + name)
            except Exception as exc:                           # noqa: BLE001
                print('  FAIL  %s: %s' % (name, exc))
                fails += 1
    return fails


if __name__ == '__main__':
    sys.exit(1 if _main() else 0)
