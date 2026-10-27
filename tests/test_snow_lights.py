"""Snow Village's window lights must not alternate luminance at 60 Hz.

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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import echo_s_flevel as E
import lgp


def _echo_section(name):
    path = os.path.join(ROOT, 'cache', 'Echo-s', 'Echo-S', 'flevel.lgp', name)
    with open(path, 'rb') as handle:
        payload = handle.read()
    try:
        return lgp.split_sections(payload)[0]
    except ValueError:
        return lgp.split_sections(lgp.lzs_decompress(payload[4:]))[0]


def test_snow_window_lights_change_only_the_four_palette_values():
    original = _echo_section('snow')
    patched, count = E.steady_snow_window_lights(original, 'snow')
    assert count == 2
    assert len(patched) == len(original)
    assert [(i, a, b) for i, (a, b) in enumerate(zip(original, patched))
            if a != b] == [(0xA3F, 57, 61), (0xA49, 66, 61),
                           (0xA73, 57, 61), (0xA7D, 66, 61)]
    assert E.steady_snow_window_lights(patched, 'snow') == (patched, 0)


def test_other_field_scripts_are_unchanged():
    for name in ('snmayor', 'hyou3', 'move_d'):
        original = _echo_section(name)
        assert E.steady_snow_window_lights(original, name) == (original, 0)


def test_snow_light_edit_reaches_field_merge():
    archive = lgp.Archive(os.path.join(ROOT, 'game_data_files', 'field',
                                       'flevel.lgp'))
    source = os.path.join(ROOT, 'cache', 'Echo-s', 'Echo-S',
                          'flevel.lgp', 'snow')
    with open(source, 'rb') as handle:
        echo_payload = handle.read()
    raw, imported = E.merge_field_payload(archive.index['snow']['payload'],
                                          echo_payload, field_name='snow')
    assert 1 in imported
    script = lgp.split_sections(raw)[0]
    assert not any(loop in script for loop in E.SNOW_LIGHT_LOOPS)
