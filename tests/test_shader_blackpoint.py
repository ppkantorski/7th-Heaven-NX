#!/usr/bin/env python3
"""tests/test_shader_blackpoint.py -- BUILD 805: the background scaler's
black point, written into the sdout copy of the shader.

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
import re

import pytest

import ff7nx_shaders as S

SRC = os.path.join(nxpaths.APP, S.CUSTOM_DIR)


def _bp(path):
    m = re.search(r'^const float HD_BLACK_POINT = ([0-9.]+);', open(path).read(),
                  re.M)
    return float(m.group(1)) if m else None


def _install(tmp_path, monkeypatch, scaler, bp):
    monkeypatch.setenv(S.SCALER_ENV, scaler)
    monkeypatch.setenv(S.FXAA_ENV, '')
    monkeypatch.setenv(S.VIDEO_ENV, '')
    if bp is None:
        monkeypatch.delenv(S.BLACK_POINT_ENV, raising=False)
    else:
        monkeypatch.setenv(S.BLACK_POINT_ENV, str(bp))
    monkeypatch.setattr(S, 'shader_dir', lambda sdout: str(tmp_path))
    return S.apply(str(tmp_path))


def test_the_max_is_the_hd_value():
    for f in ('hq4x_p.glsl', '2xsal_p.glsl'):
        for folder in ('hd', 'hq4x_lite', 'hq4x_bilinear'):
            assert abs(_bp(os.path.join(SRC, folder, f))
                       - S.BLACK_POINT_MAX / 255.0) < 1e-9, (folder, f)


def test_parsing(monkeypatch):
    for raw, want in (('', None), ('x', None), ('-3', 0), ('0', 0),
                      ('7', 7), ('12', 12), ('99', 12)):
        monkeypatch.setenv(S.BLACK_POINT_ENV, raw)
        assert S.black_point() == want, raw


@pytest.mark.parametrize('bp', [None, 12, 99])
def test_default_ships_the_files_unchanged(tmp_path, monkeypatch, bp):
    out = _install(tmp_path, monkeypatch, 'hd', bp)
    assert out
    for dest in out:
        src = os.path.join(SRC, 'hd', os.path.basename(dest))
        assert open(dest, 'rb').read() == open(src, 'rb').read()


@pytest.mark.parametrize('scaler,folder', [('hd', 'hd'),
                                           ('hd_lite', 'hq4x_lite'),
                                           ('hd_bilinear', 'hq4x_bilinear')])
def test_a_lower_value_is_written_to_the_copy_only(tmp_path, monkeypatch,
                                                   scaler, folder):
    before = {f: open(os.path.join(SRC, folder, f), 'rb').read()
              for f in os.listdir(os.path.join(SRC, folder))}
    out = _install(tmp_path, monkeypatch, scaler, 5)
    for dest in out:
        if _bp(os.path.join(SRC, folder, os.path.basename(dest))) is not None:
            assert abs(_bp(dest) - 5 / 255.0) < 1e-9, dest
    after = {f: open(os.path.join(SRC, folder, f), 'rb').read() for f in before}
    assert before == after


def test_it_never_raises_a_shader_above_its_own(tmp_path, monkeypatch):
    own = _bp(os.path.join(SRC, 'sharp', '2xsal_p.glsl'))
    assert own < S.BLACK_POINT_MAX / 255.0
    for dest in _install(tmp_path, monkeypatch, 'sharp', 12):
        src = os.path.join(SRC, 'sharp', os.path.basename(dest))
        assert open(dest, 'rb').read() == open(src, 'rb').read()


def test_a_set_without_one_is_left_alone(tmp_path, monkeypatch):
    for dest in _install(tmp_path, monkeypatch, 'xbr', 0):
        src = os.path.join(SRC, 'xbr', os.path.basename(dest))
        assert open(dest, 'rb').read() == open(src, 'rb').read()
