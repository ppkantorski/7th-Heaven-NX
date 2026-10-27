"""BUILD 640: the startup logo movie (ff7nx_sqlogo).

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
from types import SimpleNamespace as NS

import pytest

HERE = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, HERE)

import ff7nx_sqlogo as Q          # noqa: E402
import ff7nx_audio_cave as AC     # noqa: E402
import exe_patch                  # noqa: E402

DUMP_MAIN = os.path.join(HERE, 'dump', 'exefs', 'main')
DUMP_EXE = os.path.join(HERE, 'dump', 'romfs', 'ff7', 'resources', 'ff7_1.02', 'ff7_en')
need_dump = pytest.mark.skipif(not os.path.isfile(DUMP_MAIN), reason='needs the game dump')

ECHO = NS(filename='Echo-s.iro', manifest=NS(mod_id=Q.ECHO_S_MOD_ID))
SYW = NS(filename='[Tsunamods].SYW.Unified.Spells.Textures.V1.03.iro', manifest=None)
OTHER = NS(filename='CosmosFMV30.iro', manifest=None)


def test_rule():
    assert Q.choose([OTHER, ECHO, SYW]) == '_sqlogo'
    assert Q.choose([OTHER, ECHO]) == '_eidoslogo'
    assert Q.choose([OTHER, SYW]) is None
    assert Q.choose([]) is None


@need_dump
@pytest.mark.parametrize('movie', ['_sqlogo', '_eidoslogo'])
def test_exe_and_module(tmp_path, movie):
    data = open(DUMP_EXE, 'rb').read()
    pe = exe_patch.parse_pe(data)
    off = lambda va: exe_patch.va_to_offset(pe, va)          # noqa: E731
    new = Q.patch_exe(data, off, movie)
    at, _ = Q._slot_for(movie)
    assert new[off(at):off(at) + len(movie) + 5] == movie.encode() + b'.avi\0'
    e0, e1 = struct.unpack_from('<2I', new, off(Q.TABLE))
    assert e0 == at and e1 != at                                    # played once, at boot
    other = new[off(e1):off(e1) + 16].split(b'\0')[0]
    assert other in (b'eidoslogo.avi', b'sqlogo.avi')
    assert (b'sqlogo' in other) == (movie == '_eidoslogo')         # its refusal is kept
    assert Q.patch_exe(new, off, movie) == new                       # a rerun is fine
    diff = [i for i in range(len(data)) if data[i] != new[i]]
    assert all(off(at) <= i < off(at) + 16 or off(Q.TABLE) <= i < off(Q.TABLE) + 8 for i in diff)
    out = tmp_path / 'main'
    Q.apply_to_nso(DUMP_MAIN, str(out), movie)
    a = AC.segments(open(DUMP_MAIN, 'rb').read())[1][0]
    b = AC.segments(open(out, 'rb').read())[1][0]
    changed = [i for i in range(0, len(a), 4) if a[i:i + 4] != b[i:i + 4]]
    assert changed == [Q.SQ_SITE if movie == '_sqlogo' else Q.EI_SITE]
    Q.apply_to_nso(str(out), str(tmp_path / 'again'), movie)
