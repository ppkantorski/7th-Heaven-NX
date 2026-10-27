"""BUILD 642: a stereo SFX slot is rebuilt stereo (world map went silent).

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

import pytest

HERE = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, HERE)

import audio_dat as A        # noqa: E402
import sfxmod                # noqa: E402

FMT = os.path.join(HERE, 'dump', 'romfs', 'ff7', 'workingdir', 'data', 'sound', 'audio.fmt')
need = pytest.mark.skipif(not os.path.isfile(FMT) or not shutil.which('ffmpeg'),
                          reason='needs the dump and ffmpeg')


@need
def test_stereo_slot_stays_stereo(tmp_path):
    dat = FMT[:-3] + 'dat'
    van = A.read(FMT, dat)
    stereo = [i for i, e in enumerate(van) if not e.empty and e.channels == 2]
    assert stereo, 'the vanilla archive has stereo slots'
    slot = van[stereo[0]]
    src = tmp_path / 'tone.wav'
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-f', 'lavfi', '-i',
                    'sine=frequency=440:duration=0.5', str(src)], check=True)
    _cached, wav = sfxmod._encode_cached(str(src), slot, str(tmp_path))
    e = A.entry_from_wav(wav, like=slot)
    assert e.channels == slot.channels == 2
    assert e.block_align == slot.block_align
    mono = van[next(i for i, x in enumerate(van) if not x.empty and x.channels == 1)]
    _c, wav1 = sfxmod._encode_cached(str(src), mono, str(tmp_path))
    assert A.entry_from_wav(wav1).channels == 1

