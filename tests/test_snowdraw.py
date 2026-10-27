"""BUILD 542: Gold Saucer snowboard draw distance.

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
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import ff7nx_snowdraw as S  # noqa: E402
import nso_patcher  # noqa: E402

EXE = os.path.join(HERE, 'dump', 'romfs', 'ff7', 'resources', 'ff7_1.02',
                   'ff7_en')
MAIN = os.path.join(HERE, 'dump', 'exefs', 'main')
Q = lambda *_: None


def test_stock_matches_embedded():
    data = open(EXE, 'rb').read()
    assert S.check(data) == []
    routes, _pe, _rd = S._routes(data)
    for k in S.GROW_ROUTES:
        assert routes[k]['look'] == S._STOCK[k]


def test_budgets_and_monotone_and_never_lower():
    data = open(EXE, 'rb').read()
    routes, _pe, _rd = S._routes(data)
    for k in S.GROW_ROUTES:
        r = routes[k]
        st, new = S._STOCK[k], S.plan(S._STOCK[k], r)
        sp = list(S.SPAWNS[r['base']:r['base'] + r['n']])
        for i in range(r['n']):
            assert new[i] >= st[i]
            if new[i] > st[i]:
                assert i + new[i] <= r['n'] - 1
                b, q, p = S._window(i, new[i], r, sp)
                assert b <= S.MAX_BLOCKS and q <= S.MAX_QUEUE and p <= S.MAX_POOL
            if i:
                assert (i + new[i]) >= (i - 1 + new[i - 1]) or \
                    new[i - 1] == st[i - 1]


def test_exe_roundtrip_and_scope():
    d = tempfile.mkdtemp()
    try:
        p = os.path.join(d, 'ff7_en')
        shutil.copy(EXE, p)
        assert S.apply_exe(p, log=Q) == 0
        once = open(p, 'rb').read()
        stock = open(EXE, 'rb').read()
        routes, pe, _rd = S._routes(stock)
        allowed = set()
        for k in S.GROW_ROUTES:
            o = pe.off(routes[k]['look_va'])
            allowed |= set(range(o, o + routes[k]['n']))
        sroutes, _p, _r = S._routes(stock, story=True)
        for k in S.STORY_GROW_ROUTES:
            o = pe.off(sroutes[k]['look_va'])
            allowed |= set(range(o, o + sroutes[k]['n']))
        diff = [i for i, (a, b) in enumerate(zip(stock, once)) if a != b]
        assert diff and set(diff) <= allowed
        assert S.apply_exe(p, log=Q) == 0 and open(p, 'rb').read() == once
        assert S.apply_exe(p, revert=True, log=Q) == 0
        assert open(p, 'rb').read() == stock
    finally:
        shutil.rmtree(d)


def test_far_plane_roundtrip():
    d = tempfile.mkdtemp()
    try:
        p = os.path.join(d, 'main')
        shutil.copy(MAIN, p)
        os.environ[S.FAR_ENV] = '1'
        assert S.apply_nso(p, log=Q) == 0
        t = S.struct.unpack_from('<I', next(
            bytes(s.data) for s in nso_patcher.read_nso(
                nso_patcher.Path(p)).segments if s.name == '.text'),
            S.FAR_SITE)[0]
        assert t == S.FAR_NEW
        assert S.apply_nso(p, log=Q) == 0
        os.environ[S.FAR_ENV] = '0'
        assert S.apply_nso(p, log=Q) == 0          # =0: back to stock
        del os.environ[S.FAR_ENV]
        a = nso_patcher.read_nso(nso_patcher.Path(p))
        b = nso_patcher.read_nso(nso_patcher.Path(MAIN))
        ta = next(bytes(s.data) for s in a.segments if s.name == '.text')
        tb = next(bytes(s.data) for s in b.segments if s.name == '.text')
        assert ta == tb
    finally:
        shutil.rmtree(d)


def test_new_blocks_stay_inside_stock_far_plane():
    data = open(EXE, 'rb').read()
    routes, _pe, _rd = S._routes(data)
    for k in S.GROW_ROUTES:
        r = routes[k]
        st, new = S._STOCK[k], S.plan(S._STOCK[k], r)
        for i in range(r['n']):
            if new[i] > st[i]:
                d = S._dist(r['pos'][i], r['pos'][i + new[i]])
                ds = S._dist(r['pos'][i], r['pos'][min(r['n'] - 1, i + st[i])])
                assert d <= max(S._limits()[1], ds) + 1e-6
                assert S._limits()[1] < 32768          # inside the far plane


# BUILD 617c: the story course and the default far plane
def test_story_stock_matches_and_budgets_hold():
    data = open(EXE, 'rb').read()
    assert S.check_story(data) == []
    routes, _pe, _rd = S._routes(data, story=True)
    assert [r['n'] for r in routes] == [301, 50, 50, 50, 50, 50, 50]
    for k in S.STORY_GROW_ROUTES:
        r = routes[k]
        assert r['look'] == S._STORY_STOCK[k]
        new = S.plan(S._STORY_STOCK[k], r, S.STORY_SPAWNS)
        sp = list(S.STORY_SPAWNS[r['base']:r['base'] + r['n']])
        for i in range(r['n']):
            assert new[i] >= r['look'][i]
            if new[i] > r['look'][i]:
                assert i + new[i] <= r['n'] - 1
                b, q, p = S._window(i, new[i], r, sp)
                assert b <= S.MAX_BLOCKS and q <= S.MAX_QUEUE \
                    and p <= S.MAX_POOL


def test_far_plane_is_default_and_story_can_be_left_out(monkeypatch):
    monkeypatch.delenv(S.FAR_ENV, raising=False)
    assert S.far_enabled() and S._limits() == (S.K_FAR, S.FAR_DIST)
    monkeypatch.setenv(S.FAR_ENV, '0')
    assert S._limits() == (S.K, S.MAX_DIST)
    monkeypatch.setenv(S.STORY_ENV, '0')
    data = open(EXE, 'rb').read()
    _new, stats = S.build(data)
    assert not any(str(st[0]).startswith('story') for st in stats)
