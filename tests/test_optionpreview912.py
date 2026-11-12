"""BUILD 912: option previews -- the parser keeps each value's PreviewFile,
and the loader reads and shrinks it straight from the mod.

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

import tempfile  # noqa: E402

import pytest  # noqa: E402

import iro  # noqa: E402
import nxpreview as P  # noqa: E402

XML = '''<ModInfo><ID>12345678-1234-1234-1234-123456789abc</ID><Name>T</Name>
<ConfigOption><Type>List</Type><Default>0</Default><ID>Spells</ID>
<Name>Spell Names</Name>
<Option Value="0" Name="Off" PreviewFile="off.png"/>
<Option Value="1" Name="On" PreviewFile="Preview\\on.png"/>
<Option Value="2" Name="None"/>
</ConfigOption></ModInfo>'''


def test_parser_keeps_previews():
    with tempfile.TemporaryDirectory() as d:
        p = _nx_os.path.join(d, 'mod.xml')
        with open(p, 'w') as fh:
            fh.write(XML)
        opt = iro.Manifest(p).options[0]
    assert opt.previews == {0: 'off.png', 1: 'Preview\\on.png'}
    assert [v for v, _l in opt.values] == [0, 1, 2]


def test_loader_on_a_folder_mod():
    from PIL import Image
    with tempfile.TemporaryDirectory() as d:
        with open(_nx_os.path.join(d, 'mod.xml'), 'w') as fh:
            fh.write(XML)
        _nx_os.makedirs(_nx_os.path.join(d, 'Preview'))
        Image.new('RGB', (1280, 896), (200, 10, 10)).save(
            _nx_os.path.join(d, 'Preview', 'on.png'))
        # an animated GIF, 3 frames
        fr = [Image.new('RGB', (64, 64), c) for c in ('red', 'lime', 'blue')]
        fr[0].save(_nx_os.path.join(d, 'off.png'), format='GIF',
                   save_all=True, append_images=fr[1:], duration=50, loop=0)
        big = P.load_frames(d, 'Preview\\on.png')
        assert len(big) == 1
        assert big[0][0].size[0] <= P.MAX_W and big[0][0].size[1] <= P.MAX_H
        assert big[0][0].size == (420, 294)          # aspect kept
        anim = P.load_frames(d, 'off.png')
        assert len(anim) == 3 and all(ms >= 20 for _f, ms in anim)
        assert P.load_frames(d, 'missing.png') == []


def test_real_mod_if_installed():
    path = _nx_os.path.join(_nx_root, 'mods',
                            '[Tsunamods]_Modern_Spell_Names.iro')
    if not _nx_os.path.exists(path):
        pytest.skip('mod not installed')
    frames = P.load_frames(path, 'on.png')
    assert frames and frames[0][0].size[0] <= P.MAX_W
