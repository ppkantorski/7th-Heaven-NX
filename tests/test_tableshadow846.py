"""BUILD 846: mds7pb_1 table -- the floor shadow behind a solid is hidden with
the depth FFNx really draws it at (_ffnx_shift), and the solid outline.

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
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src', 'field'))
import numpy as np
import ff7nx_floordepth as FD

A, B = 1.01627, 130.09                 # mds7pb_1's depth curve (620h)


def test_ffnx_offset_is_about_34_units_at_mds7pb_1_depth():
    c = np.array([5400.0])             # camera depth of the floor there
    cfg = {'ffnx_bias': True, 'pad': 0.0}
    got = FD._ffnx_shift(cfg, A, B, c - FD.HOLE_BIAS)
    assert 28.0 < float(c - got) < 40.0
    # without the flag the depth is left as it was (games_2's HOLE_BIAS)
    assert FD._ffnx_shift({}, A, B, c)[0] == c[0]
    assert np.isinf(FD._ffnx_shift(cfg, A, B, np.array([np.inf])))[0]


def test_solid_marks_only_its_outline():
    R = np.eye(3)
    o = np.array([0.0, 0.0, 100.0])    # looking down -z... any non-degenerate camera
    R = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], float)
    gx, gy = np.meshgrid(np.arange(0, 40, 1.0), np.arange(0, 40, 1.0))
    cls = np.zeros(gx.shape, int)
    hi = np.full(gx.shape, np.inf)
    lo = np.full(gx.shape, -np.inf)
    cfg = {'solid': [((10.0, 10.0), (30.0, 10.0), (30.0, 30.0), (10.0, 30.0))]}
    c2, h2, l2, ins = FD._solid(cfg, R, o, 100, gx, gy, cls, hi, lo)
    assert ins.sum() == 400 and (c2[ins] == 1).all() and (c2[~ins] == 0).all()
    assert np.isinf(h2[~ins]).all()
    assert FD._solid({}, R, o, 100, gx, gy, cls, hi, lo)[3].sum() == 0


def test_mds7pb_1_is_configured_after_the_floor_pass():
    cfg = FD.HOLE_FIELDS['mds7pb_1']
    assert cfg['ffnx_bias'] and cfg['pad'] >= 24 and cfg['solid']
    import inspect
    src = inspect.getsource(FD.apply_to_flevel)
    assert src.index('for name in FIELDS') < src.index('for name in HOLE_FIELDS')
