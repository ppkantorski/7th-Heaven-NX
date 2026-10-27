"""BUILD 579: static palettes on a mixed additive FX page get Cosmos clones.

Runs on the real mod and the shipped archive when they are present (the
chorace save crystal: palette 10, one Cosmos DDS, on page 15 beside five
animated racing-light palettes).

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
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
PROJ = os.path.expanduser('~/mnt/7th_heaven_nx')
if PROJ not in sys.path:
    sys.path.append(PROJ)

import iro  # noqa: E402
import lgp  # noqa: E402
import field_bg_native as FN  # noqa: E402
import field_bg_repack as FR  # noqa: E402
import ff7nx_fxsplit as S  # noqa: E402

IRO = os.path.join(PROJ, 'mods', 'CosmosLimitBreak.iro')
# The VANILLA archive: the shipped one already carries this pass's output.
FLEVEL = os.path.join(PROJ, 'dump', 'romfs', 'ff7', 'workingdir', 'data',
                      'field', 'flevel.lgp')
_ART = {}


def _art():
    if 'a' not in _ART:
        m = iro.Manifest(os.path.join(PROJ, 'cache', 'CosmosLimitBreak',
                                      'mod.xml'))
        opts = {'LIMITBREAK': 1, 'UNCROP': 1, 'ATA': 1}
        folders = iro.active_folders(m, opts)
        assert 'AA REMOVED' not in folders          # BUILD 579 <Not>
        allowed = {f.lower().replace('\\', '/').rstrip('/') + '/'
                   for f in folders}

        class A:
            pass
        a = A()
        a.provider = FR.ArtProvider([(IRO, allowed)], 768)
        _ART['a'] = a
    return _ART['a']


def _field(name):
    with open(FLEVEL, 'rb') as h:
        h.read(12)
        n = struct.unpack('<i', h.read(4))[0]
        toc = [h.read(27) for _ in range(n)]
        for e in toc:
            if e[:20].split(b'\0')[0].decode().lower() == name:
                off = struct.unpack('<I', e[20:24])[0]
                h.seek(off)
                sz = struct.unpack('<I', h.read(24)[20:24])[0]
                return lgp.lzs_decompress(h.read(sz)[4:])


def _have():
    return os.path.exists(IRO) and os.path.exists(FLEVEL)


def test_chorace_crystal_moves_and_lights_stay():
    if not _have():
        return
    s9 = lgp.split_sections(_field('chorace'))[8]
    new9, st = S.split_section9('chorace', s9, _art(), 768)
    assert (st['pages'], st['tiles'], st['kept_animated']) == (1, 6, 183)
    old_pl, ots, _ = FN.parse_texture_block(s9, 768)
    pl, ts, _ = FN.parse_texture_block(new9, 768)
    assert ts == ots
    new = {p.slot: p for p in pl if p} 
    old = {p.slot: p for p in old_pl if p}
    assert set(new) - set(old) == {16}
    assert (new[16].depth, new[16].px) == (2, 768)
    assert new[15].data == old[15].data            # animated page untouched
    diff = [i for i in range(ts) if s9[i] != new9[i]]
    assert len(diff) == 6
    for i in diff:                                 # only FX page bytes
        assert (s9[i], new9[i]) == (15, 16)
        assert new9[i - FN.TILE_TEXTURE_ID2 + 22] == 10   # palette 10


def test_all_animated_or_all_static_pages_are_left_alone():
    if not _have():
        return
    os.environ[S.FREEZE_ENV] = '0'
    try:
        for name in ('jet', 'games'):
            s9 = lgp.split_sections(_field(name))[8]
            new9, st = S.split_section9(name, s9, _art(), 768)
            assert new9 is s9 and st['pages'] == 0
    finally:
        del os.environ[S.FREEZE_ENV]


def test_jet_sign_stays_animated_by_default_freeze_is_opt_in():
    """BUILD 581: freezing the sign overlapped its text on hardware, so the
    default is the 1997 animated sign; 'jet:9,13' still freezes on request."""
    if not _have():
        return
    assert S.freeze_map({}) == {}
    s9 = lgp.split_sections(_field('jet'))[8]
    new9, st = S.split_section9('jet', s9, _art(), 768)
    assert new9 is s9 and st['pages'] == 0
    os.environ[S.FREEZE_ENV] = 'jet:9,13'
    try:
        new9, st = S.split_section9('jet', s9, _art(), 768)
    finally:
        del os.environ[S.FREEZE_ENV]
    assert st['pages'] == 2 and st['frozen'] == st['tiles'] == 28
    _pl, ts, _ = FN.parse_texture_block(new9, 768)
    for i in range(ts):
        if s9[i] != new9[i]:
            assert new9[i - FN.TILE_TEXTURE_ID2 + 22] in (9, 13)
    assert S.freeze_map({S.FREEZE_ENV: 'a:1,2;b:3'}) == {
        'a': frozenset((1, 2)), 'b': frozenset((3,))}


def test_switch_off_and_budget_veto():
    if not _have():
        return
    s9 = lgp.split_sections(_field('chorace'))[8]
    os.environ[S.OFF_ENV] = '1'
    try:
        assert S.split_section9('chorace', s9, _art(), 768)[0] is s9
    finally:
        del os.environ[S.OFF_ENV]
    new9, st = S.split_section9('chorace', s9, _art(), 768,
                                max_runtime_delta=1000)
    assert new9 is s9 and st['veto_budget'] == 1
