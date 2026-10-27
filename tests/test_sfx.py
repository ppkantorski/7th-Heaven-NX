#!/usr/bin/env python3
"""
test_sfx.py -- applying an FFNx sound mod through audio.fmt / audio.dat.

    python3 test_sfx.py [--mod DIR]

WHAT IT IS FOR
==============
The Switch port has no external sound-effect path -- every .ogg string in
exefs/main points at data/music_ogg. An FFNx sound mod's loose files are dead
on the SD card. What is NOT dead is `audio.fmt` / `audio.dat`, the 750-slot
archive the PC game has always used, so the mod can be applied by rebuilding
that pair with its audio in the slots its own `config.toml` names.

The earlier conclusion -- "Cosmo Memory cannot work at all, <1% of the mod's
IDs are in range" -- came from comparing the mod's OGG FILENAMES against the
game's sound ids. Those are different numbering schemes and config.toml is
the map between them. Through it the mod reaches 720 of the 750 slots.

WHAT IS CHECKED
===============
The archive format first, because everything rests on it: a pair that is read
and written unchanged must come back byte for byte, including the empty slots
whose format block is 18 bytes rather than 50. Then the mapping, the layering
of several folders, and that a rebuild leaves untouched slots alone.

With `--mod DIR` pointing at an extracted sound mod the real config.toml is
parsed as well.

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
import shutil
import struct
import subprocess
import sys
import tempfile

import audio_dat
import sfxmod

FAIL = []


def check(cond, msg):
    print(('  ok    ' if cond else '  FAIL  ') + msg)
    if not cond:
        FAIL.append(msg)


def have_ffmpeg():
    return bool(shutil.which('ffmpeg'))


def mkwav(freq=440, dur='0.03', rate=22050):
    return subprocess.run(
        ['ffmpeg', '-y', '-nostdin', '-loglevel', 'error', '-f', 'lavfi',
         '-i', 'sine=frequency=%d:duration=%s:sample_rate=%d'
         % (freq, dur, rate),
         '-ac', '1', '-acodec', 'adpcm_ms', '-f', 'wav', '-'],
        stdout=subprocess.PIPE, check=True).stdout


print('the archive format\n')
check(audio_dat.HEADER_LEN == 24, 'FfWav_header is 24 bytes')
check(audio_dat.FMT_FULL == 50,
      'a used slot carries a 50-byte ADPCMWAVEFORMAT')
check(audio_dat.FMT_EMPTY == 18,
      'an EMPTY slot carries only 18 -- getting this wrong desynchronises '
      'every record after the first empty one')

if not have_ffmpeg():
    print('\nffmpeg not found -- the encoding checks are skipped')
else:
    ents = []
    for i in range(audio_dat.NUM_SLOTS):
        if i % 11 == 5:
            ents.append(audio_dat.Entry())
            continue
        fmt, data, _s, _e = audio_dat.parse_wav(
            mkwav(200 + i % 600, '0.02', 11025 if i % 2 else 22050))
        ents.append(audio_dat.Entry(fmt, data, 1 if i % 90 == 0 else 0,
                                    i % 4, 0, 96 if i % 90 == 0 else 0))
    fmt_b, dat_b = audio_dat.dumps(ents)

    print('\nread and write are exact inverses\n')
    back = audio_dat.loads(fmt_b, dat_b)
    check(len(back) == audio_dat.NUM_SLOTS,
          'all %d slots come back' % audio_dat.NUM_SLOTS)
    check(all((a.data or b'') == (b.data or b'') for a, b in zip(ents, back)),
          'sample data survives')
    check(all((a.fmt or b'') == (b.fmt or b'') for a, b in zip(ents, back)),
          'format blocks survive')
    check(all((a.loop, a.count, a.start, a.end)
              == (b.loop, b.count, b.start, b.end)
              for a, b in zip(ents, back)),
          'loop points and the count field survive')
    check(audio_dat.dumps(back) == (fmt_b, dat_b),
          'and a second write is byte for byte identical')
    check(sum(1 for e in back if e.empty) == sum(1 for e in ents if e.empty),
          'empty slots stay empty rather than being filled in')

    print('\na rebuild only touches what it is told to\n')
    tmp = tempfile.mkdtemp()
    ogg = os.path.join(tmp, '900.ogg')
    subprocess.run(['ffmpeg', '-y', '-nostdin', '-loglevel', 'error',
                    '-f', 'lavfi', '-i', 'sine=frequency=880:duration=0.05',
                    '-ac', '1', ogg], check=True)
    fresh = audio_dat.loads(fmt_b, dat_b)
    before = [(e.data or b'') for e in fresh]
    # BUILD 759b: sound 150 is muted by default; this check is about what a
    # rebuild is TOLD to replace, so the mute list is emptied for it.
    os.environ[sfxmod.MUTE_ENV] = 'none'
    try:
        res = sfxmod.rebuild(fresh, {7: [900]}, {900: ogg}, cache_dir=tmp)
    finally:
        os.environ.pop(sfxmod.MUTE_ENV, None)
    check(res.replaced == 1, 'one slot replaced (%d)' % res.replaced)
    changed = [i for i, e in enumerate(fresh) if (e.data or b'') != before[i]]
    check(changed == [6],
          'and it is slot 7 (1-based) and nothing else -- changed %s'
          % [c + 1 for c in changed])
    check(fresh[6].sample_rate == back[6].sample_rate,
          'the replacement keeps the slot\'s own sample rate (%s), because '
          'the engine pitches some effects relative to it'
          % fresh[6].sample_rate)

    print('\nan empty slot is left alone\n')
    fresh2 = audio_dat.loads(fmt_b, dat_b)
    res2 = sfxmod.rebuild(fresh2, {6: [900]}, {900: ogg}, cache_dir=tmp)
    check(res2.replaced == 0 and res2.skipped_empty == [6],
          'a mapping onto a slot the game never uses is skipped, not '
          'invented (%s)' % res2.skipped_empty)

    # HARDWARE, 2026-07-29: the game FROZE after a victory. A freeze is a wait
    # that never completes, and the one value in this whole path that was
    # invented rather than read is the loop end of a replaced looping slot --
    # a field whose units sfxEdit's own readme marks with a question mark. A
    # sound that never reports finishing hangs anything waiting on it, and
    # the post-battle sequence waits on sounds.
    print('\na looping slot is LEFT ALONE, not given invented loop points\n')
    loops = [i for i, e in enumerate(back) if e.loop]
    if loops:
        i = loops[0]
        fresh3 = audio_dat.loads(fmt_b, dat_b)
        before3 = fresh3[i].data
        res3 = sfxmod.rebuild(fresh3, {i + 1: [900]}, {900: ogg},
                              cache_dir=tmp)
        check(fresh3[i].data == before3 and res3.skipped_loop == [i + 1],
              'slot %d loops in the game\'s own archive, so it keeps the '
              'game\'s own sound (%s)' % (i + 1, res3.skipped_loop))
        check(res3.replaced == 0, 'and nothing was written for it')

        os.environ[sfxmod.LOOPS_ENV] = 'replace'
        fresh4 = audio_dat.loads(fmt_b, dat_b)
        sfxmod.rebuild(fresh4, {i + 1: [900]}, {900: ogg}, cache_dir=tmp)
        check(fresh4[i].data != before3 and fresh4[i].loop == 0,
              'SEVENTH_NX_SFX_LOOPS=replace takes it, with looping OFF so it '
              'plays once rather than to an end marker nobody can compute')
        os.environ.pop(sfxmod.LOOPS_ENV)

        e = audio_dat.entry_from_wav(mkwav(440, '0.05', 22050),
                                     like=back[i])
        check(e.loop == 0 and e.end == 0,
              'entry_from_wav never invents a loop range on its own either')
    shutil.rmtree(tmp, ignore_errors=True)

print('\nconfig.toml\n')
CFG = '''# a comment
[6]
sequential = [ 6303 ]

[563]
sequential = [ 2108, 2109, 2110 ]

[battle_enemy_0080_35]
sequential = [ 9999 ]
'''
cfg = sfxmod.parse_config(CFG)
check(cfg == {6: [6303], 563: [2108, 2109, 2110]},
      'numeric sections are read and the per-enemy ones ignored (%s)' % cfg)
check(sfxmod.choose([2108, 2109, 2110]) == 2108,
      'a rotating set collapses to its first entry -- FFNx alternates, the '
      'port cannot')
os.environ[sfxmod.PICK_ENV] = 'last'
check(sfxmod.choose([2108, 2109, 2110]) == 2110,
      'and %s=last takes the other end' % sfxmod.PICK_ENV)
os.environ.pop(sfxmod.PICK_ENV)

print('\nfolders layer, later winning\n')
check(sfxmod.merge_configs([CFG, '[6]\nsequential = [ 1 ]\n'])[6] == [1],
      'a later folder overrides an id from an earlier one')
tmp = tempfile.mkdtemp()
for rel in ('Base/sfx/config.toml', 'Base/sfx/10.ogg', 'UI_R/sfx/10.ogg',
            'Base/Ambient/5.ogg', 'Base/sfx/notanumber.ogg'):
    p = os.path.join(tmp, *rel.split('/'))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, 'w').write(CFG if rel.endswith('.toml') else 'x')
files = [(r, os.path.join(tmp, *r.split('/'))) for r in
         ('Base/sfx/config.toml', 'Base/sfx/10.ogg', 'UI_R/sfx/10.ogg',
          'Base/Ambient/5.ogg', 'Base/sfx/notanumber.ogg')]
configs, oggs = sfxmod.collect(files)
check(len(configs) == 1, 'one config found')
check(set(oggs) == {10}, 'only numeric .ogg under sfx/ join the pool (%s)'
      % sorted(oggs))
check(oggs[10].endswith(os.path.join('UI_R', 'sfx', '10.ogg')),
      'and the later folder\'s copy wins')
check(not any('Ambient' in v for v in oggs.values()),
      'Ambient/ is not swept in -- it needs a loader the port does not have')
shutil.rmtree(tmp, ignore_errors=True)

mod_dir = None
for i, a in enumerate(sys.argv):
    if a == '--mod' and i + 1 < len(sys.argv):
        mod_dir = sys.argv[i + 1]
if mod_dir and os.path.isdir(mod_dir):
    print('\nagainst the real mod: %s\n' % os.path.basename(mod_dir))
    files = []
    for folder in sorted(os.listdir(mod_dir)):
        base = os.path.join(mod_dir, folder)
        if not os.path.isdir(base):
            continue
        for dp, _d, fs in os.walk(base):
            for fn in sorted(fs):
                full = os.path.join(dp, fn)
                files.append((os.path.relpath(full, mod_dir), full))
    configs, oggs = sfxmod.collect(files)
    cfg = sfxmod.merge_configs(configs)
    check(len(cfg) > 600,
          '%d sound ids mapped -- not the "<1%% in range" the old note '
          'claimed' % len(cfg))
    check(max(cfg) <= audio_dat.NUM_SLOTS,
          'every mapped id is inside slots 1..%d (highest %d)'
          % (audio_dat.NUM_SLOTS, max(cfg)))
    have = sum(1 for i in cfg if sfxmod.choose(cfg[i]) in oggs)
    check(have > 600,
          '%d of them have their .ogg in the enabled folders' % have)

print('\n%s' % ('FAILED: %d' % len(FAIL) if FAIL else 'all checks passed'))
sys.exit(1 if FAIL else 0)
