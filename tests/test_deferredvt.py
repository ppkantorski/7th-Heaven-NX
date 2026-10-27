"""BUILD 550: gfx_drv_draw_deferred keeps TLVERTEX primitives as TLVERTEX.

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
import os, shutil, struct, sys, tempfile
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import ff7nx_deferredvt as D  # noqa: E402
import nso_patcher  # noqa: E402

STOCK = os.path.join(HERE, 'dump', 'exefs', 'main')


def _text(path):
    nso = nso_patcher.read_nso(nso_patcher.Path(path))
    return next(bytes(s.data) for s in nso.segments if s.name == '.text')


def test_stock_anchors():
    t = _text(STOCK)
    assert D.check_text(t) == [] and D.state_text(t) == 'stock'


def test_patch_revert_round_trip():
    d = tempfile.mkdtemp()
    try:
        p = os.path.join(d, 'main')
        shutil.copyfile(STOCK, p)
        assert D.apply_nso(p, revert=False, log=lambda *a: None) == 0
        t = _text(p)
        assert D.state_text(t) == 'patched'
        stock = _text(STOCK)
        diff = {i for i in range(len(t)) if t[i] != stock[i]}
        allowed = set()
        for va, _s, _n in D.WORDS:
            allowed |= set(range(va, va + 4))
        assert diff and diff <= allowed
        # idempotent
        assert D.apply_nso(p, revert=False, log=lambda *a: None) == 0
        assert _text(p) == t
        assert D.apply_nso(p, revert=True, log=lambda *a: None) == 0
        assert _text(p) == stock
    finally:
        shutil.rmtree(d)


def test_env_switch():
    assert D.enabled({})
    assert not D.enabled({D.ENV: '0'})
