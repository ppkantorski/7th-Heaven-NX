"""BUILD 567: SYW's Fort Condor map4 realigned (ff7nx_condormap).

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

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import ff7nx_condormap as M  # noqa: E402

SYW = os.path.join(HERE, 'mods',
                   '[Tsunamods].SYW.Unified.MiniGames.Textures.V1.04.iro')
CONDOR = os.path.join(HERE, 'dump', 'romfs', 'ff7', 'workingdir', 'data',
                      'minigame', 'condor.lgp')


def _page(i):
    import iro
    import ff7nx_ddstex
    import lgp
    d = iro.read_one(SYW, 'FF7SYWU\\Base\\condor\\map%d_00.dds' % i)
    d = list(d.values())[0] if isinstance(d, dict) else d
    src = lgp.Archive(CONDOR).index['map%d.tex' % i]['payload']
    return src, d, ff7nx_ddstex._decode(d)[0]


def test_synthetic_shift_is_found_and_undone():
    rng = np.random.default_rng(1)
    van = np.zeros((64, 64, 4), np.uint8)
    van[:, :, :3] = rng.integers(0, 255, (64, 64, 3))
    van[:, :, 3] = 255
    from PIL import Image
    up = np.asarray(Image.fromarray(van).resize((256, 256), Image.NEAREST))
    low = np.zeros_like(up)
    low[24:] = up[:-24]                    # the page drawn 6 units low
    dy, dx, err, zero = M.measure(low, van)
    assert (dy, dx) == (-6, 0) and err < M.GAIN * zero
    fixed = M.realign(low, van, dy, dx)
    assert np.array_equal(fixed[:-24], up[:-24])


def test_real_pages():
    if not (os.path.exists(SYW) and os.path.exists(CONDOR)):
        return
    for i in range(8):
        src, dds, rgba = _page(i)
        van = M._vanilla_rgba(src)
        dy, dx, err, zero = M.measure(rgba, van)
        if i == 4:
            assert (dy, dx) == (-6, 0) and err < M.GAIN * zero
            out = M.fix_blobs('condor.lgp', 'map4.tex', src, {0: dds})[0]
            assert M.measure(out, van)[:2] == (0, 0)
        else:
            assert (dy, dx) == (0, 0), i
            out = M.fix_blobs('condor.lgp', 'map%d.tex' % i, src, {0: dds})
            assert out[0] is dds                 # untouched


def test_only_condor_map_pages():
    assert M.applies('condor.lgp', 'map4.tex')
    assert M.applies('condorj.lgp', 'MAP0.TEX')
    assert not M.applies('condor.lgp', 'mes02.tex')
    assert not M.applies('coaster.lgp', 'map4.tex')
