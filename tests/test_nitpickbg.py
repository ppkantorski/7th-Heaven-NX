"""BUILD 643: Nitpick's background fixes patch only the cells they change.

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
import sys

import numpy as np

HERE = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, HERE)

import ff7nx_nitpickbg as NB     # noqa: E402
import dds_decode                # noqa: E402


def _page(seed):
    g = np.random.default_rng(seed)
    img = g.integers(40, 200, (256, 256, 4)).astype(np.uint8)
    img[:, :, 3] = 255
    return img


def test_only_the_fixed_cells_are_copied():
    clb = _page(1)
    nit = clb.copy()
    nit[32:48, 64:96, :3] = 255 - nit[32:48, 64:96, :3]      # a 2-cell "sign" fix
    out, n, _why = NB.patch_cells(nit, clb)
    assert n == 2
    assert (out[32:48, 64:96] == nit[32:48, 64:96]).all()
    keep = np.ones((256, 256), bool)
    keep[32:48, 64:96] = False
    assert (out[keep] == clb[keep]).all()                       # everything else is Cosmos


def test_a_different_layout_is_left_to_cosmos():
    out, n, why = NB.patch_cells(_page(2), _page(3))
    assert out is None and n == 0 and 'differently' in why


def test_too_many_cells_is_cosmos_own_art():
    clb = _page(4)
    nit = clb.copy()
    nit[:48, :, :3] = 255 - nit[:48, :, :3]                     # 48 cells
    out, n, _why = NB.patch_cells(nit, clb)
    assert out is None


def test_uncompressed_dds_round_trip(tmp_path):
    img = _page(5)
    p = tmp_path / 'x.dds'
    NB.write_dds(str(p), img)
    rgba, w, h = dds_decode.decode_dds(open(p, 'rb').read())
    assert (w, h) == (256, 256)
    assert (np.frombuffer(rgba, np.uint8).reshape(256, 256, 4) == img).all()


def test_area_limits_the_fix():
    clb = _page(6)
    nit = clb.copy()
    nit[32:48, 64:80, :3] = 255 - nit[32:48, 64:80, :3]      # inside the area
    nit[160:176, 160:176, :3] = 255 - nit[160:176, 160:176, :3]  # elsewhere
    out, n, _why = NB.patch_cells(nit, clb, area=(1, 3, 3, 6))
    assert n == 1
    assert (out[160:176, 160:176] == clb[160:176, 160:176]).all()


def test_pixel_area_takes_only_changed_pixels():
    """BUILD 644b: a 'px' area (the md8_2 truck label) copies the letters,
    not whole cells -- re-render noise around them stays Cosmos's."""
    import numpy as np
    import ff7nx_nitpickbg as NB
    clb = np.full((256, 256, 4), 100, np.uint8)
    nit = clb.copy()
    nit[:, :, :3] += 6                       # re-render noise everywhere
    nit[100:104, 180:190, :3] = 250          # the "letters", inside cell (6, 11)
    out, n, why = NB.patch_cells(nit, clb, (6, 6, 11, 13, 'px'))
    assert n == 40 and 'pixel' in why
    assert (out[101, 185, :3] > 200).all()   # letters taken
    assert (out[20, 20] == clb[20, 20]).all()          # far away: Cosmos
    assert (out[96, 200] == clb[96, 200]).all()        # same cells, no change


def test_sign_glow_feathers_above_the_board():
    """BUILD 644g: the glow above the board fades out smoothly, no hard cut."""
    import numpy as np
    import ff7nx_nitpickbg as NB
    spec = NB.FEATHER_EXAMPLE
    img = np.full((1024, 1024, 4), 200, np.uint8)
    out = NB.trim_glow(img, spec)
    col = np.concatenate([out[0:64, 700, 3], out[64:128, 700, 3]]).astype(int)
    assert col[0] == 0 and col[-1] == 200                 # out at top, full at board
    assert (np.diff(col) >= 0).all()                      # never steps back up
    assert np.abs(np.diff(col)).max() <= 12               # no abrupt edge
    assert (out[64:128, 0:448] == img[64:128, 0:448]).all()   # others untouched
    assert (out[..., :3] == img[..., :3]).all()
    assert NB.GLOW_TRIMS == {}        # 644g: Cosmos's own glow is kept
