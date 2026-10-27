"""BUILD 668: sichi's lightning from Cosmos's crisp base sheet, the 1997
fade kept as light levels (ff7nx_lightning).

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

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import lgp                                                    # noqa: E402
import ff7nx_lightning as L                                   # noqa: E402

VAN = os.path.join(ROOT, 'game_data_files', 'field', 'flevel.lgp')
COS = os.path.join(ROOT, 'mods', 'CosmosLimitBreak.iro')


@pytest.fixture(scope='module')
def sichi():
    if not os.path.exists(VAN):
        pytest.skip('no vanilla flevel')
    a = lgp.Archive(VAN)
    return list(lgp.split_sections(a.decompressed(a.index['sichi'])))


BUILT = os.path.join(ROOT, 'attic', 'dirs', 'test_662', 'atmosphere', 'contents',
                     '0100A5B00BDC6000', 'romfs', 'ff7', 'workingdir', 'data',
                     'field', 'flevel.lgp')


def _one(path, name):
    """one field's sections, read straight from a (1.4 GB) built archive"""
    with open(path, 'rb') as f:
        f.read(lgp.CREATOR_LEN)
        n = struct.unpack('<i', f.read(4))[0]
        for _ in range(n):
            e = f.read(lgp.TOC_ENTRY_LEN)
            if e[:20].split(b'\0')[0].decode().lower() == name:
                off = struct.unpack('<I', e[20:24])[0]
                break
        else:
            raise KeyError(name)
        f.seek(off + 20)
        p = f.read(struct.unpack('<I', f.read(4))[0])
    return list(lgp.split_sections(lgp.lzs_decompress(p[4:])))


@pytest.fixture(scope='module')
def built():
    if not os.path.exists(BUILT):
        pytest.skip('no built flevel (test_662)')
    return _one(BUILT, 'sichi')


@pytest.fixture(scope='module')
def art():
    if not os.path.exists(COS):
        pytest.skip('no Cosmos iro')
    os.environ['SEVENTH_NX_FIELD_BG_PAGE_PX'] = '768'
    import field_bg_repack as FR
    import ff7nx_marginart as MA
    return MA.provider_source(FR.ArtProvider(
        [(COS, {'limit break/', 'limit break aa/', 'limit break aaf/'})],
        768, lambda *a: None))


def test_levels_follow_the_1997_line():
    v = [L.level_v(j) for j in range(L.LEVELS)]
    assert v == sorted(v) and 0 < v[0] < 8 and 58 < v[-1] < 64
    steps = np.diff(v)
    assert np.allclose(steps, steps[0])
    assert L.P0 + L.LEVELS - 1 < 64 and L.P0 > 1          # params sichi lacks


def test_script_patch_decodes_and_drives_every_level(sichi):
    import echo_s_flevel as ES
    s0 = L.patch_script(sichi[0])
    assert len(s0) == len(sichi[0])
    on = {}
    for start, end in ES._routine_blocks(s0):
        st, _ = ES._decode_block(s0, start, end)
        ops = [(s0[o], s0[o + 1:o + sz]) for o, op, sz in st]
        for op, arg in ops:
            assert op not in (0xE5, 0xE6, 0xE7, 0xEA) or arg[2] not in (
                10, 11, 12, 13) or False
        sets = [a for op, a in ops if op == 0x80 and a[0] == 0x50 and a[1] < 4]
        for a in sets:
            on[a[1]] = (a[2], [x for op, x in ops if op in (0xE0, 0xE1)])
    assert sorted(on) == [0, 1, 2, 3]
    for k, (top, bg) in on.items():
        assert top == L.P0 + L.LEVELS - 1
        assert bytes((0x50, k, k)) in bg                  # BGON/OFF var,k
    # no palette op on the bolt palettes is left
    import ff7nx_palanim as PA
    assert not ({10, 11, 12, 13} & set(PA.animated_palettes(s0)))


def test_plan_matches_the_1997_bolts(sichi, built, art):
    import ff7nx_framesim as FS
    s0, s9, info = L.plan_field('sichi', list(built), list(sichi), art)
    assert info['removed'] == 167 and info['mb'] < 35.0
    assert info['added'] > 167 * (L.LEVELS - 2)
    new = list(built)
    new[0], new[8] = s0, s9
    V, N = FS.Field(list(sichi)), FS.Field(new)
    B = FS.Field(list(built))
    cam = (0, -150)
    for st in (1, 2, 4, 8):
        van = V.render(cam=cam, states={1: st}, K=1, mark=False).astype(float)
        base = V.render(cam=cam, K=1, mark=False).astype(float)
        top = N.render(cam=cam, states={L.P0 + L.LEVELS - 1: st}, K=1,
                       mark=False).astype(float)
        nbase = N.render(cam=cam, K=1, mark=False).astype(float)
        dv, dn = (van - base).mean(2), (top - nbase).mean(2)
        m = dv > 8
        assert m.sum() > 50
        c = np.corrcoef(dv[m], dn[m])[0, 1]
        assert c > 0.9, (st, c)                           # the 1997 bolt
        ratio = dn[m].sum() / dv[m].sum()
        assert 0.85 < ratio < 1.05, (st, ratio)           # v 61/64 of full
        # every level is dimmer than the next
        prev = 0.0
        for j in range(L.LEVELS):
            im = N.render(cam=cam, states={L.P0 + j: st}, K=1,
                          mark=False).astype(float)
            e = (im - nbase).clip(0).sum()
            assert e > prev
            prev = e
    # nothing else of the built picture moved
    assert (B.render(cam=cam, K=1, mark=False)
            == N.render(cam=cam, K=1, mark=False)).all()


def test_off_switch(monkeypatch):
    monkeypatch.setenv(L.OFF_ENV, '1')
    assert L.apply_to_flevel(None, {}, art=object()) == {'names': [],
                                                          'refused': []}
