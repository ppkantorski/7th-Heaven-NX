"""BUILD 644: a mod sound with nothing above 300 Hz keeps the game's own.

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
import subprocess
import sys

import pytest

HERE = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, HERE)

import audio_dat as A        # noqa: E402
import sfxmod                # noqa: E402

FMT = os.path.join(HERE, 'dump', 'romfs', 'ff7', 'workingdir', 'data', 'sound', 'audio.fmt')
need = pytest.mark.skipif(not os.path.isfile(FMT) or not shutil.which('ffmpeg'),
                          reason='needs the dump and ffmpeg')


def _tone(tmp_path, hz, name):
    src = tmp_path / ('%s.wav' % name)
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-f', 'lavfi', '-i',
                    'sine=frequency=%d:duration=1:sample_rate=44100' % hz,
                    str(src)], check=True)
    return str(src)


@need
def test_sub_bass_replacement_keeps_vanilla(tmp_path, monkeypatch):
    monkeypatch.delenv(sfxmod.INAUDIBLE_ENV, raising=False)
    van = A.read(FMT, FMT[:-3] + 'dat')
    entries = list(van)
    oggs = {1: _tone(tmp_path, 50, 'rumble'), 2: _tone(tmp_path, 1000, 'tone')}
    # 433 is WEAPON's roar; 11 the Highwind bump -- both audible in vanilla
    res = sfxmod.rebuild(entries, {433: [1], 11: [2]}, oggs,
                         cache_dir=str(tmp_path / 'c'))
    assert [k[0] for k in res.kept_inaudible] == [433]
    assert entries[432] is van[432]
    assert res.replaced_ids == [11]
    assert any('kept the game' in line for line in sfxmod.describe(res))


@need
def test_keep_setting_ships_the_mod_sound(tmp_path, monkeypatch):
    monkeypatch.setenv(sfxmod.INAUDIBLE_ENV, 'keep')
    van = A.read(FMT, FMT[:-3] + 'dat')
    entries = list(van)
    res = sfxmod.rebuild(entries, {433: [1]}, {1: _tone(tmp_path, 50, 'rumble')},
                         cache_dir=str(tmp_path / 'c'))
    assert res.replaced_ids == [433] and not res.kept_inaudible
