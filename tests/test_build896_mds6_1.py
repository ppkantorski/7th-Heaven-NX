"""BUILD 896: mds6_1 -- the stakes' jagged edges anti-aliased (ff7nx_edgeaa)
and the low-road shadow kept off the upper road (ff7nx_floordepth holes).

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

import numpy as np

import ff7nx_edgeaa as EA                                     # noqa: E402
import ff7nx_floordepth as FD                                 # noqa: E402


def _staircase(n=60, step=3):
    """A dark region below a diagonal edge drawn in `step`-texel steps."""
    C = np.full((n, n, 3), 200.0, np.float32)
    for y in range(n):
        x_edge = (y // step) * step
        C[y, :x_edge] = 40.0
    return C


def _roughness(C):
    lum = C.mean(-1)
    # per row, the edge position as the luminance-weighted crossing
    pos = ((200.0 - lum) / 160.0).sum(1)
    return float(np.abs(np.diff(pos, 2)).mean())


def test_staircase_becomes_smoother_and_keeps_its_dark():
    C = _staircase()
    valid = np.ones(C.shape[:2], bool)
    out, ch = EA.smooth_edges(C, valid, valid.copy())
    assert ch.any()
    assert _roughness(out) < 0.5 * _roughness(C)
    # the dark area keeps its ink (within 5 %)
    ink = lambda c: float((200.0 - c.mean(-1)).sum())       # noqa: E731
    assert abs(ink(out) - ink(C)) < 0.05 * ink(C)
    # nothing away from the edge changes
    far = np.ones_like(valid)
    for y in range(C.shape[0]):
        e = (y // 3) * 3
        far[y, max(0, e - 6):e + 6] = False
    assert np.array_equal(out[far], C[far])


def test_thin_line_stays_a_dark_line():
    C = np.full((40, 40, 3), 200.0, np.float32)
    for x in range(40):
        C[20 + (x // 3) % 2, x] = 40.0          # a 1-texel zigzag
    valid = np.ones(C.shape[:2], bool)
    out, _ = EA.smooth_edges(C, valid, valid.copy())
    col = out[:, 10:30].mean(-1).min(0)          # darkest per column
    assert (col < 120).all()                     # still reads as a line


def test_flat_art_untouched():
    C = np.full((30, 30, 3), 120.0, np.float32)
    C += np.random.default_rng(1).normal(0, 6, C.shape).astype(np.float32)
    valid = np.ones(C.shape[:2], bool)
    out, ch = EA.smooth_edges(C, valid, valid.copy())
    assert not ch.any() and np.array_equal(out, C)


def test_mds6_1_configured():
    assert 'mds6_1' in EA.TARGETS
    cfg = FD.HOLE_FIELDS['mds6_1']
    assert cfg['plane'] == 4.0 and cfg['grid'] == (0, 8) and cfg['ffnx_bias']


def test_texels_default_plane_is_the_old_behaviour():
    R = np.array([[0.7, -0.7, 0.0], [-0.57, -0.56, -0.6],
                  [0.43, 0.42, -0.8]])
    o = np.array([171.0, -196.0, 4383.0])
    tris = np.array([[[-200, -200, 0], [200, -200, 0], [0, 200, 0]],
                     [[300, 0, 40], [500, 0, 40], [400, 200, 40]]], float)
    gx, gy = np.meshgrid(np.linspace(-60, 60, 7), np.linspace(-60, 60, 7))
    a = FD._texels(R, o, 1194, tris, gx, gy)
    b = FD._texels(R, o, 1194, tris, gx, gy, 0.0)
    for x, y in zip(a, b):
        assert np.array_equal(np.nan_to_num(x), np.nan_to_num(y))


def test_switches(monkeypatch):
    monkeypatch.setenv(EA.OFF_ENV, '1')
    assert EA.disabled()
    st = EA.apply_to_flevel(None, {})
    assert st == {'names': [], 'refused': []}


def _stake_scene():
    """A warm stake (lit left half, wide shaded right face) on a dark road,
    with a 1-texel black outline round it and a black speck in the road."""
    C = np.full((60, 60, 3), (60.0, 58.0, 55.0), np.float32)       # dark road
    C[10:50, 20:30] = (200.0, 170.0, 120.0)                         # lit wood
    C[10:50, 30:36] = (120.0, 100.0, 70.0)                          # shaded face
    C[9, 19:37] = C[50, 19:37] = 10.0                               # outline
    C[9:51, 19] = C[9:51, 36] = 10.0
    C[30, 12] = C[30, 13] = 5.0                                     # speck
    return C


def test_896c_shaded_face_kept_outline_and_speck_gone():
    C = _stake_scene()
    v = np.ones(C.shape[:2], bool)
    out, ch, val = EA.fix_outlines(C, v, v)
    lum = out @ np.array([0.3, 0.59, 0.11], np.float32)
    lum0 = C @ np.array([0.3, 0.59, 0.11], np.float32)
    # the wide shaded face is the art's own: untouched
    assert np.allclose(out[14:46, 31:35], C[14:46, 31:35])
    # the black speck in the road is gone
    assert lum[30, 12] > 40 and lum[30, 13] > 40
    # the outline on the lit side (against the road) is lifted out of black
    assert lum[20:40, 19].mean() > lum0[20:40, 19].mean() + 20


def test_896c_dark_seam_between_light_sides_pinched():
    """A 1-texel dark seam between the stake and the light marking."""
    C = np.full((60, 60, 3), (60.0, 58.0, 55.0), np.float32)
    C[10:50, 20:30] = (200.0, 170.0, 120.0)                          # stake
    C[5:55, 31:50] = (225.0, 215.0, 190.0)                           # marking
    C[10:50, 30] = 70.0                                              # seam
    v = np.ones(C.shape[:2], bool)
    out, ch, val = EA.fix_outlines(C, v, v)
    lum = out @ np.array([0.3, 0.59, 0.11], np.float32)
    assert lum[15:45, 30].min() > 120
