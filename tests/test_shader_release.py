#!/usr/bin/env python3
"""tests/test_shader_release.py -- shaders the release ships.

"Native" means the game's own file is used, which the port does by finding
no replacement in the mod's romfs. So nothing the project ships may be a
copy of a game shader: such a file changes nothing and would redistribute
the game's file. Checked against the user's dump when there is one.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install()
import os

import pytest

import ff7nx_shaders as S

ROOT = nxpaths.ROOT
STOCK = os.path.join(ROOT, 'dump', 'romfs', 'ff7', 'shaders')


def _shipped():
    files = S.release_files()
    if not os.path.isdir(os.path.join(ROOT, S.CUSTOM_DIR)):
        pytest.skip('no shaders/ folder')
    return files


def test_release_list_covers_every_set_and_no_stock():
    files = S.release_files()
    assert 'shaders/wide_screen/lmain_vv.glsl' in files
    assert 'shaders/hd/2xsal_p.glsl' in files
    assert not any(f.startswith('shaders/stock/') for f in files)
    # the HD family's depth pass is native, so it is not shipped
    for folder in ('hd', 'hq4x_lite', 'hq4x_bilinear'):
        assert 'shaders/%s/2xsal_depth_p.glsl' % folder not in files
    assert 'shaders/xbr/2xsal_depth_p.glsl' in files
    # switching any set off still prunes every name a set ever wrote
    assert '2xsal_depth_p.glsl' in S.ALL_FILES


def test_every_shipped_file_exists():
    missing = [f for f in _shipped()
               if not os.path.isfile(os.path.join(ROOT, f))]
    assert not missing, missing


def test_no_shipped_file_is_a_game_shader():
    files = _shipped()
    if not os.path.isdir(STOCK):
        pytest.skip('no dump')
    same = []
    for f in files:
        stock = os.path.join(STOCK, os.path.basename(f))
        if os.path.isfile(stock):
            with open(os.path.join(ROOT, f), 'rb') as a, \
                    open(stock, 'rb') as b:
                if a.read() == b.read():
                    same.append(f)
    assert not same, same
