"""BUILD 641: the Nitpick mod (ff7nx_nitpick).

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

import ff7nx_nitpick as N        # noqa: E402
import a64 as A                  # noqa: E402

DUMP_MAIN = os.path.join(HERE, 'dump', 'exefs', 'main')
need_dump = pytest.mark.skipif(not os.path.isfile(DUMP_MAIN), reason='needs the game dump')
NIT = NS(manifest=NS(mod_id=N.MOD_ID, name='Nitpick'), filename='Nitpick.iro')
OTHER = NS(manifest=NS(mod_id='x', name='Other'), filename='o.iro')


def test_locked_and_effective():
    assert N.locked_option(NIT, 'NITPICK-TITLEMUSIC')
    assert N.locked_option(NIT, 'optic-blast')
    assert N.locked_option(NIT, 'NITPICK-TITLE') is None
    assert N.locked_option(OTHER, 'NITPICK-TITLEMUSIC') is None
    eff = N.effective({'NITPICK-TITLEMUSIC': 1, 'LIGHT-WORLD': 2, 'NITPICK-TITLE': 2})
    assert eff['NITPICK-TITLEMUSIC'] == 0 and eff['LIGHT-WORLD'] == 0
    assert eff['NITPICK-TITLE'] == 2 and eff['ffnx_aspect_ratio'] == 0


def test_carried_hext():
    assert N.carried_hext('/c/Nitpick/title_screen_midgar43/hext/start.txt')
    assert N.carried_hext('credits_vanilla\\credits\\hext\\02.txt')
    assert not N.carried_hext('/c/Nitpick/other/hext/x.txt')


def test_texture_jobs_later_folder_wins():
    plan = {'folders': ['avatar_red/red_fix_sywu_ui/', 'title_screen_midgar43/',
                        'pixelback/']}
    entries = ['avatar_red\\Red_Fix_SYWU_UI\\Menu\\red_00.dds',
               'pixelback\\menu\\red_00.dds',
               'title_screen_midgar43\\menu\\buster_00.dds',
               'title_screen_midgar43\\hext\\start.txt',
               'credits_hd\\credits\\cr\\ff7logo_a_00.dds']
    jobs = N.texture_jobs(plan, entries)
    assert jobs == {'menu_us.lgp': {'red.tex': 'pixelback\\menu\\red_00.dds',
                                    'buster.tex': 'title_screen_midgar43\\menu\\buster_00.dds'}}


@need_dump
def test_module_words():
    import ff7nx_audio_cave as AC
    import ff7nx_cave
    import nxmap
    text = AC.segments(open(DUMP_MAIN, 'rb').read())[1][0]
    pool = ff7nx_cave.HolePool(bytearray(text), starts=set(nxmap.Main(DUMP_MAIN).arm_starts))
    w = N.module_words(text, {'title': True, 'title_menu': True, 'credits': True}, pool)
    assert w[N.CREDITS_LOOP] == (A.sub_imm(9, 8, 6), A.sub_imm(9, 8, 1))
    for va, (reg, axis) in N.TITLE_READS.items():
        assert w[va][1] == A.movz(reg, N.TITLE_X if axis == 'x' else N.TITLE_Y + 2 * N.TITLE_ROW)
    for va in list(N.TITLE_QUAD) + list(N.CREDITS_QUAD):
        assert (w[va][1] >> 26) == 0b000101          # b cave
    assert N.module_words(text, {}, pool) == {}


def test_never_switches_other_mods_off():
    """BUILD 641b: log 393 -- Nitpick's background fixes turned Cosmos Limit
    Break's field art off through its compatibility rules."""
    assert N.ignores_rule(NIT, '0') and N.ignores_rule(NIT, 0)
    assert not N.ignores_rule(NIT, 3)                 # 60 FPS mode for Diamond 60FPS
    assert not N.ignores_rule(OTHER, 0)
    on = {k: 1 for k in N.BG_FIXES}
    assert all(N.effective(on, clb=True)[k] == 1 for k in N.BG_FIXES)   # 641c: kept
    assert N.locked_option(NIT, 'NITPICK-BGMD8_2', clb=True) is None


def test_ultima_world_skeleton_draws_its_neck(tmp_path):
    """BUILD 644c: Nitpick's dyb.hrc lists EEC1 twice and never EEC."""
    import ff7nx_nitpick as N
    hrc = tmp_path / 'dyb.hrc'
    hrc.write_bytes(b':HEADER_BLOCK 2\r\n:SKELETON ultima_sk\r\n:BONES 29\r\n\r\n'
                    b'head\r\nchest\r\n13.0314\r\n3 EEC1 EEC1 EEC2\r\n')
    rsd = tmp_path / 'eec.rsd'
    rsd.write_bytes(b'@RSD940102\r\n')
    files = {'dyb.hrc': (str(hrc), None), 'eec.rsd': (str(rsd), None)}
    out = N.fix_world_files(files, str(tmp_path / 'c'))
    data = open(out['dyb.hrc'][0], 'rb').read()
    assert b'3 EEC EEC1 EEC2\r\n' in data and b'EEC1 EEC1' not in data
    assert out['eec.rsd'] == files['eec.rsd']
    # any other skeleton is left alone
    other = tmp_path / 'x.hrc'
    other.write_bytes(b':SKELETON diaweap_sk\r\n3 EEC1 EEC1 EEC2\r\n')
    files['dyb.hrc'] = (str(other), None)
    assert N.fix_world_files(files, str(tmp_path / 'c'))['dyb.hrc'] == files['dyb.hrc']
