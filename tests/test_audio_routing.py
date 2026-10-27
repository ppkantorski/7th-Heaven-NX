#!/usr/bin/env python3
"""
test_audio_routing.py -- which .ogg files are music and which are not.

    python3 test_audio_routing.py

WHAT THE SWITCH PORT ACTUALLY READS
===================================
exefs/main contains exactly three external-asset format strings:

    %s/data/music_ogg/%s.ogg
    %s/data/movies/%s.mp4
    %s/data/png/%s.%s

That is the whole list. Music is streamed from music_ogg -- which is why a
soundtrack replacement works by copying .ogg files in -- and NOTHING else in
the port reads an .ogg. Sound effects still come from data/sound/audio.dat
indexed by audio.fmt, loaded by the recompiled sound.cpp: the loader walks
exactly 0x2EE = 750 entries of 0x1C bytes at 0xDE0E84, in both the Steam exe
and the Switch 1.03_5 exe.

So an FFNx sound mod's .ogg files are not music and must not be copied as
music. They used to be, and `plan.music` is keyed on basename, so they also
overwrote each other and -- in Cosmo Memory's case -- a real music track.

The fixture below is the directory shape of
[Tsunamods] Cosmo Memory v1.21, including the `Base/vgmstream/Wind.ogg`
entry that collided with the `wind` music track.

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
import sys
import tempfile

import build
import iro

FAIL = []


def check(cond, msg):
    print(('  ok    ' if cond else '  FAIL  ') + msg)
    if not cond:
        FAIL.append(msg)


def plan_for(tree, manifest_body, music_names=None):
    """Build a one-mod plan over a temporary tree laid out like an extracted
    .iro, and return it."""
    root = tempfile.mkdtemp()
    cache = os.path.join(root, 'cache')
    moddir = os.path.join(cache, 'TestMod')
    for rel in tree:
        p = os.path.join(moddir, rel.replace('/', os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'wb').write(b'\0' * 16)
    with open(os.path.join(moddir, 'mod.xml'), 'w') as f:
        f.write('<?xml version="1.0"?>\n<ModInfo>\n<ID>t</ID><Name>T</Name>\n'
                + manifest_body + '\n</ModInfo>\n')

    iro_path = os.path.join(root, 'TestMod.iro')
    open(iro_path, 'wb').write(b'')
    mod = build.Mod(iro_path, cache)
    mod._load_manifest()
    settings = mod.manifest.defaults() if mod.manifest else {}
    return build.build_plan([mod], {mod.filename: settings}, {},
                            lambda *a, **k: None, music_names=music_names)


# The shape of an FFNx sound mod.
SOUND_TREE = [
    'Base/sfx/0.ogg', 'Base/sfx/1024.ogg', 'Base/sfx/6724.ogg',
    'Base/vgmstream/Wind.ogg',
    'Ambience/Ambient/1500.ogg', 'Ambience/Ambient/37.ogg',
    'V_Attacks/sfx/1100.ogg', 'NV_Attacks/sfx/1100.ogg',
    'UI_R/sfx/6024.ogg',
    'FMV/movies/BIGLIGHT.ogg', 'FMV/lang-en/movies/ENDING2.ogg',
    'ECHOS/voice/nvmkin21/28.ogg',
]
SOUND_XML = '\n'.join('<ModFolder Folder="%s"/>' % f for f in
                      ('Base', 'Ambience', 'V_Attacks', 'NV_Attacks',
                       'UI_R', 'FMV', 'ECHOS'))

print('an FFNx sound mod (Cosmo Memory\'s layout)\n')
plan = plan_for(SOUND_TREE, SOUND_XML)
check(plan.music == {},
      'no .ogg routed to data/music_ogg (got %d)' % len(plan.music))
# sfx/ is no longer among these. It used to be, on the grounds that the port
# has no external SFX path -- true, and the wrong conclusion. audio.fmt /
# audio.dat is a path, and sfx/ is exactly what rebuilds it, so those files
# are now collected for that instead of thrown away. See test_sfx.py.
sfx_n = sum(1 for f in SOUND_TREE if '/sfx/' in f)
check(len(plan.sfx) == sfx_n,
      'the %d sfx/ file(s) are collected for the audio.dat rebuild, not '
      'discarded (got %d)' % (sfx_n, len(plan.sfx)))
# Ambient/ left this list for the same reason sfx/ did, one feature later.
# The port has no external AMBIENCE path either -- also true, also the wrong
# conclusion: `data/music_ogg` is a path the port's own OGG player already
# resolves, and ff7nx_ambient drives one independent player against it. So
# these are collected for that rather than thrown away. See test_ambient.py.
amb_n = sum(1 for f in SOUND_TREE if '/ambient/' in f.lower())
check(len(plan.ambient) == amb_n,
      'the %d Ambient/ file(s) are collected for the ambient runtime, not '
      'discarded (got %d)' % (amb_n, len(plan.ambient)))
# And voice/ left the list one feature after that, for the third time and the
# same reason. The port has no external VOICE path either -- and again the
# path that matters is `data/music_ogg`, which MusicStream resolves by NAME.
# So an Echo-S-shaped clip is collected for the dialogue runtime now instead
# of being thrown away. See test_voice_staging.py.
voice_n = sum(1 for f in SOUND_TREE if '/voice/' in f.lower())
check(len(plan.voice) == voice_n,
      'the %d voice/ file(s) are collected for the dialogue runtime, not '
      'discarded (got %d)' % (voice_n, len(plan.voice)))
movie_n = 2      # FMV/movies/BIGLIGHT.ogg and FMV/lang-en/movies/ENDING2.ogg
check(len(plan.movie_audio) == movie_n,
      'the %d cutscene soundtracks are COLLECTED, not discarded -- they are '
      'muxed into the movie mp4 (got %s)'
      % (movie_n, sorted(plan.movie_audio)))
check(sum(plan.skipped_ffnx_audio.values())
      == len(SOUND_TREE) - sfx_n - amb_n - voice_n - movie_n,
      'and the remaining %d are counted as FFNx audio (got %s)'
      % (len(SOUND_TREE) - sfx_n - amb_n - voice_n - movie_n,
         plan.skipped_ffnx_audio))
check('wind.ogg' not in plan.music,
      'Base/vgmstream/Wind.ogg does not overwrite the `wind` music track: '
      'this mod\'s single vgmstream entry is outnumbered by its sfx, so the '
      'whole directory reads as aliases')
check(set(plan.skipped_ffnx_audio) == {'vgmstream'},
      'the aliases are still recognised and skipped -- that one needs a '
      'loader this port does not have (%s)' % sorted(plan.skipped_ffnx_audio))
check('ambient' not in plan.skipped_ffnx_audio
      and 'voice' not in plan.skipped_ffnx_audio
      and 'movies' not in plan.skipped_ffnx_audio,
      'and none of ambience, voice or cutscene audio is among them any more '
      '-- all three have somewhere to go now')

# A soundtrack replacement: tracks sit at the top of the mod folder.
MUSIC_TREE = ['Arranged/oa.ogg', 'Arranged/ti.ogg', 'Arranged/wind.ogg',
              'Arranged/aseri2.ogg']
MUSIC_XML = '<ModFolder Folder="Arranged"/>'

print('\na soundtrack replacement (the mod that already works)\n')
plan = plan_for(MUSIC_TREE, MUSIC_XML)
check(len(plan.music) == 4,
      'all 4 tracks still routed to music_ogg (got %d)' % len(plan.music))
check(set(plan.music) == {'oa.ogg', 'ti.ogg', 'wind.ogg', 'aseri2.ogg'},
      'by basename, unchanged: %s' % sorted(plan.music))
check(not plan.skipped_ffnx_audio,
      'nothing skipped (got %s)' % plan.skipped_ffnx_audio)

# The mixed case: a mod with both.
print('\na mod carrying both\n')
plan = plan_for(MUSIC_TREE + SOUND_TREE, MUSIC_XML + '\n' + SOUND_XML,
                music_names={'oa.ogg', 'ti.ogg', 'wind.ogg', 'aseri2.ogg'})
check(set(plan.music) == {'oa.ogg', 'ti.ogg', 'wind.ogg', 'aseri2.ogg'},
      'the 4 real tracks survive and nothing else joins them: %s'
      % sorted(plan.music))
check(plan.music['wind.ogg'][0].endswith(os.path.join('Arranged', 'wind.ogg')),
      'wind.ogg is the MUSIC one, not the vgmstream sound effect')
check(sum(plan.skipped_ffnx_audio.values()) + len(plan.sfx)
      + len(plan.ambient) + len(plan.voice)
      + len(plan.movie_audio) == len(SOUND_TREE),
      'every sound-mod file is accounted for -- skipped, collected for the '
      'audio.dat rebuild, for the ambient runtime, or for the dialogue '
      'runtime (%s, %d sfx, %d ambient, %d voice)'
      % (plan.skipped_ffnx_audio, len(plan.sfx), len(plan.ambient),
         len(plan.voice)))

# Case-insensitivity: FFNx mods are inconsistent about capitalisation.
print('\ndirectory names are matched case-insensitively\n')
plan = plan_for(['X/SFX/1.ogg', 'X/Ambient/2.ogg', 'X/Movies/3.ogg'],
                '<ModFolder Folder="X"/>')
check(plan.music == {},
      'SFX/, Ambient/, Movies/ all recognised regardless of case (%s)'
      % sorted(plan.music))


# ---------------------------------------------------------------------------
# REGRESSION, 2026-07-28: `vgmstream` was added to FFNX_AUDIO_DIRS and every
# soundtrack mod that ships the FFNx way became a silent no-op.
# [Tsunamods] Arranged Soundtrack: 94 candidate files, 91 under vgmstream/,
# all dropped, the log calling it "skipped, no Switch loader".
#
# vgmstream is the audio backend FFNx plays MUSIC through. `music/vgmstream/
# <name>.ogg` is where a track replacement belongs.
# ---------------------------------------------------------------------------
VGM_MUSIC_TREE = ['Arranged/vgmstream/oa.ogg',
                  'Arranged/vgmstream/ti.ogg',
                  'Arranged/vgmstream/wind.ogg',
                  'Arranged/vgmstream/aseri2.ogg',
                  'Arranged/vgmstream/cephiros.ogg']
VGM_XML = '<ModFolder Folder="Arranged"/>'
TRACKS = {'oa.ogg', 'ti.ogg', 'wind.ogg', 'aseri2.ogg', 'cephiros.ogg',
          'ketc.ogg', 'yume.ogg'}

print('\na soundtrack that ships under vgmstream/ (the regression)\n')
plan = plan_for(VGM_MUSIC_TREE, VGM_XML, music_names=TRACKS)
check(len(plan.music) == 5,
      'all 5 tracks routed to music_ogg (got %d)' % len(plan.music))
check(plan.music_from_vgmstream == 5,
      'and all 5 counted as coming from vgmstream/ (got %d)'
      % plan.music_from_vgmstream)
check(not plan.skipped_ffnx_audio,
      'nothing skipped (got %s)' % plan.skipped_ffnx_audio)

print('\nthe same mod with no track list available\n')
plan = plan_for(VGM_MUSIC_TREE, VGM_XML, music_names=None)
check(len(plan.music) == 5,
      'still all 5 -- no dump must never mean no soundtrack (got %d)'
      % len(plan.music))

print('\na sound mod whose vgmstream names are mostly NOT tracks\n')
ALIAS_TREE = ['Base/vgmstream/Wind.ogg', 'Base/vgmstream/Door.ogg',
              'Base/vgmstream/Elevator.ogg', 'Base/vgmstream/Rain.ogg']
plan = plan_for(ALIAS_TREE, '<ModFolder Folder="Base"/>', music_names=TRACKS)
check(plan.music == {},
      'none taken as music -- 1 of 4 names is a real track, so the mod reads '
      'as aliases (got %s)' % sorted(plan.music))
check(plan.skipped_ffnx_audio.get('vgmstream') == 4,
      'all 4 counted as FFNx audio (got %s)' % plan.skipped_ffnx_audio)

print('\na soundtrack with one stray alias inside it\n')
MIXED = VGM_MUSIC_TREE + ['Arranged/vgmstream/Footstep7.ogg']
plan = plan_for(MIXED, VGM_XML, music_names=TRACKS)
check(len(plan.music) == 5 and 'footstep7.ogg' not in plan.music,
      'the 5 real tracks are taken and the stray one is not (got %s)'
      % sorted(plan.music))


# The exact shape that broke in the field, 2026-07-28: a soundtrack mod that
# ALSO ships a couple of FFNx movie-audio overlays. The first version of the
# "does it ship FFNx sound" test was a presence check, so two files under
# movies/ condemned all 91 tracks under vgmstream/ and the mod stayed a no-op
# through a second build.
print('\na soundtrack that also carries a little movie audio\n')
REAL_TREE = (['Arranged/vgmstream/%s.ogg' % n for n in
              ('oa', 'ti', 'wind', 'aseri2', 'cephiros', 'ketc', 'yume')]
             + ['Arranged/movies/BIGLIGHT.ogg', 'Arranged/movies/ENDING2.ogg'])
plan = plan_for(REAL_TREE, '<ModFolder Folder="Arranged"/>', music_names=TRACKS)
check(len(plan.music) == 7,
      'the 7 tracks are taken -- 2 movie-audio files must not outvote them '
      '(got %d)' % len(plan.music))
check(sorted(plan.movie_audio) == [('biglight', 'music'),
                                   ('ending2', 'music')],
      'and the 2 movie overlays are collected as cutscene soundtracks '
      'rather than as music (got %s)' % sorted(plan.movie_audio))
check('movies' not in plan.skipped_ffnx_audio,
      'none of them is counted as skipped (got %s)' % plan.skipped_ffnx_audio)

print('\nCosmo Memory is still separated, and not narrowly\n')
COSMO = (['Base/sfx/%d.ogg' % i for i in range(20)]
         + ['Ambience/Ambient/%d.ogg' % i for i in range(8)]
         + ['ECHOS/voice/nvmkin21/%d.ogg' % i for i in range(6)]
         + ['Base/vgmstream/Wind.ogg'])
plan = plan_for(COSMO, '\n'.join('<ModFolder Folder="%s"/>' % f
                                 for f in ('Base', 'Ambience', 'ECHOS')),
                music_names=TRACKS)
check(plan.music == {},
      '34 sound files against 1 vgmstream entry -- none taken as music '
      '(got %s)' % sorted(plan.music))
check('wind.ogg' not in plan.music,
      'the `wind` music track is not overwritten by an ambient sound')

# --------------------------------------------- the synthesized toggle, 505
print('\na folder the mod ships with no ActiveWhen\n')
SYNTH_TREE = ['Base/sfx/1.ogg', 'Base/sfx/config.toml', 'WM/sfx/2.ogg']
SYNTH_XML = ('<ModFolder Folder="Base"/>'
             '<ModFolder Folder="WM" ActiveWhen="WM = 1"/>'
             '<ConfigOption><Type>Bool</Type><Default>1</Default>'
             '<ID>WM</ID><Name>WM</Name>'
             '<Option Value="0" Name="Off"/><Option Value="1" Name="On"/>'
             '</ConfigOption>')


def _synth_mod():
    root = tempfile.mkdtemp()
    cache = os.path.join(root, 'cache')
    moddir = os.path.join(cache, 'TestMod')
    for rel in SYNTH_TREE:
        p = os.path.join(moddir, rel.replace('/', os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'wb').write(b'\0' * 16)
    with open(os.path.join(moddir, 'mod.xml'), 'w') as f:
        f.write('<?xml version="1.0"?>\n<ModInfo>\n<ID>t</ID><Name>T</Name>\n'
                + SYNTH_XML + '\n</ModInfo>\n')
    iro_path = os.path.join(root, 'TestMod.iro')
    open(iro_path, 'wb').write(b'')
    mod = build.Mod(iro_path, cache)
    mod._load_manifest()
    return mod


_m = _synth_mod()
check('Base' in _m.manifest.synthesized,
      'a toggle is invented for the ungated folder (%s)'
      % sorted(_m.manifest.synthesized))
check('WM' not in _m.manifest.synthesized,
      'and NOT for one the mod actually declares')
check(_m.manifest.defaults().get('Base') == 1,
      'it defaults ON, so the mod ships the way its author intended')
check(_m.manifest.synthesized_off({'Base': 1, 'WM': 1}) == [],
      'nothing is reported while it is on')
check([i for i, _f in _m.manifest.synthesized_off({'Base': 0, 'WM': 1})]
      == ['Base'],
      'switching it off is reported, because 7th Heaven has no such switch '
      'and the folder is the mod\'s core content')
check(_m.manifest.synthesized_off({'Base': 1, 'WM': 0}) == [],
      'turning off a REAL option is the author\'s own choice and is silent')

_warned = []
list(_m.files_for({'Base': 0, 'WM': 1}, log=_warned.append))
check(any("'Base' is OFF" in w for w in _warned),
      'and the build log says so where the folders are resolved (%s)'
      % (_warned[:1] or ['(nothing logged)']))


print('\n%s' % ('FAILED: %d' % len(FAIL) if FAIL else 'all checks passed'))
sys.exit(1 if FAIL else 0)
