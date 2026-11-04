"""BUILD 885: junin1a / nivgate window waits, cargoin light tile.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)


import os  # noqa: E402

import pytest  # noqa: E402

import ff7nx_cave  # noqa: E402
import ff7nx_voice as V  # noqa: E402

ROOT = _nx_root
NSO = os.path.join(ROOT, 'dump', 'exefs', 'main')
EXE = os.path.join(ROOT, 'dump', 'romfs', 'ff7', 'resources', 'ff7_1.02',
                   'ff7_en')


def _need():
    if not (os.path.exists(NSO) and os.path.exists(EXE)):
        pytest.skip('needs the game dump')


def test_bodies_proven_dead_on_stock():
    _need()
    sp = V._Spill(NSO, NSO, open(EXE, 'rb').read())
    sp._prove()
    assert sp.skipped == []
    assert [b[0] for b in sp.bodies] == list(V.SPILL_X86)
    # room for the 377-word cave log 17-22-00 could not place, and more
    assert sum((hi - lo) // 4 for _x, lo, hi in sp.bodies) > 1500


def test_fallback_only_on_noroom(monkeypatch):
    _need()
    sp = V._Spill(NSO, NSO, open(EXE, 'rb').read())

    def build(entry, addr):
        return [0xD503201F] * 9 + [0xD65F03C0]

    def full(pool, b, span=None):
        raise ff7nx_cave.NoRoom('full')
    monkeypatch.setattr(V.ff7nx_cave, 'emit_laid_out', full)
    monkeypatch.setattr(V, '_SPILL', None)
    with pytest.raises(ff7nx_cave.NoRoom):
        V._emit_laid_out(None, build)
    monkeypatch.setattr(V, '_SPILL', sp)
    e1, w1 = V._emit_laid_out(None, build)
    e2, w2 = V._emit_laid_out(None, build)
    assert len(w1) == 10 and e2 >= e1 + 40 and e2 % 16 == 0
    assert sp.used[0] == (V.SPILL_X86[0], e1, 10)

    def fits(pool, b, span=None):
        return 4, {4: 0}
    monkeypatch.setattr(V.ff7nx_cave, 'emit_laid_out', fits)
    assert V._emit_laid_out(None, build) == (4, {4: 0})   # pool first

