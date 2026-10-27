"""BUILD 776: games_2's last shadow sliver, mogu_1's water (loop + rim),
md8_2's stray speck, md8_3's fountain glow edge.

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

import numpy as np  # noqa: E402
import pytest  # noqa: E402

import ff7nx_floordepth as FD  # noqa: E402
import ff7nx_fxcomplete as FC  # noqa: E402
import ff7nx_fxpages as XP  # noqa: E402
import ff7nx_strayfront as SF  # noqa: E402
import ff7nx_waterloop as W  # noqa: E402


def test_deep_polygon_pulls_only_inside():
    cfg = FD.HOLE_FIELDS['games_2']
    gx, gy = np.meshgrid([110.0, 121.0, 136.0], [176.0, 180.0, 186.0])
    hi = np.full(gx.shape, 1000.0)
    hi[0, 0] = np.inf
    out = FD._deep(cfg, gx, gy, hi)
    assert out[1, 1] == 1000.0 - cfg['deep'][0]['bias']   # (121, 180): inside
    assert (out[1, 0] == 1000.0) and (out[1, 2] == 1000.0)
    assert out[0, 1] == 1000.0 and out[2, 1] == 1000.0
    assert np.isinf(out[0, 0])
    assert 'no_shadow' not in cfg                          # 771d stays gone


# test_waterloop_single_frame_steps: BUILD 776's single-frame loop was
# replaced in BUILD 778 (it lost the pair's coverage); see test_build778.


def test_mogu_rim_rule_recolours_a_mild_dark_band():
    px, cs = 256, 16
    img = np.zeros((px, px, 4), np.uint8)
    img[:cs, :cs, :3] = 120
    img[:cs, :cs, 3] = 255
    img[:cs, :cs, 3][:, 10:] = 0          # an edge at column 10
    img[:cs, 6:10, :3] = 100              # a 4-texel rim at 0.83 of the inside
    plain = XP._edge_colour(img, px)
    mogu = XP._edge_colour(img, px, r=5, dark=0.97)
    assert (plain[:cs, 6:10, :3] == 100).all()           # default rule: kept
    assert (mogu[:cs, 6:10, :3] > 110).all()             # mogu_1: cleaned
    assert (mogu[:cs, :5, :3] == 120).all()              # inside untouched
    assert XP.AVG_EDGE_FIELD == {'mogu_1': (5, 0.97)}


def test_strayfront_islands():
    m = np.zeros((48, 48), bool)
    m[0:3, 43:48] = True
    m[20:40, 0:10] = True
    lab, n = SF._islands(m)
    assert n == 2
    assert SF.FIELDS == {'md8_2': {'max_id': 16}} and SF.MAX_ISLAND == 2


def test_feather_config():
    assert FC.FEATHER == {'md8_3': {'pals': (14,), 'sigma': 3.0}}


D = '/mnt/user-data/uploads/Claude/7th_heaven_nx/attic/_claude_tmp/'


@pytest.mark.skipif(not os.path.exists(D + 'md8_2_b775.raw'),
                    reason='needs the 775 md8_2 field')
def test_md8_2_speck_only():
    import lgp
    b = list(lgp.split_sections(open(D + 'md8_2_b775.raw', 'rb').read()))
    v = list(lgp.split_sections(open(D + 'md8_2_van.raw', 'rb').read()))
    s9, done = SF.clean(b[8], v[8], 16)
    assert done == [(112, 128, 7, 9)] and len(s9) == len(b[8])
