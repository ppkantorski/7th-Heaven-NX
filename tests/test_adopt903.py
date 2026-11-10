#!/usr/bin/env python3
"""BUILD 903: a supported mod copied into mods/ by hand under another name
(Nitpick 1.83's .7z unpacks to "7ac77aef-..._Nitpick.iro") gets the name
Install Mods would give it, so its settings, load order and cache apply;
an old copy beside it is moved to mods/.replaced/, never deleted.

    python3 -m pytest tests/test_adopt903.py

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)

import os

import ff7nx_download as D

SOURCES = [{'id': 'aaaa-1', 'name': 'Nitpick', 'file': 'Nitpick.iro'},
           {'id': 'bbbb-2', 'name': 'Folder Mod', 'file': 'Folder Mod'}]


def _mod(d, name, mid, version):
    os.makedirs(os.path.join(d, name))
    with open(os.path.join(d, name, 'mod.xml'), 'w') as f:
        f.write('<ModInfo><ID>%s</ID><Version>%s</Version></ModInfo>'
                % (mid, version))


def _version(d, name):
    return open(os.path.join(d, name, 'mod.xml')).read().split(
        '<Version>')[1].split('<')[0]


def test_a_copy_under_another_name_is_renamed(tmp_path):
    d = str(tmp_path)
    _mod(d, 'downloaded_Folder Mod v3', 'bbbb-2', '3')
    done = D.adopt_manual_copies(d, SOURCES)
    assert done == [('downloaded_Folder Mod v3', 'Folder Mod')]
    assert sorted(os.listdir(d)) == ['Folder Mod']


def test_the_newer_copy_keeps_the_name(tmp_path):
    d = str(tmp_path)
    _mod(d, 'Folder Mod', 'bbbb-2', '1.82')
    _mod(d, 'new copy', 'bbbb-2', '1.83')
    D.adopt_manual_copies(d, SOURCES)
    assert sorted(os.listdir(d)) == ['.replaced', 'Folder Mod']
    assert _version(d, 'Folder Mod') == '1.83'
    assert _version(os.path.join(d, '.replaced'), 'Folder Mod') == '1.82'


def test_an_older_or_equal_copy_is_moved_aside(tmp_path):
    d = str(tmp_path)
    _mod(d, 'Folder Mod', 'bbbb-2', '2')
    _mod(d, 'stray', 'bbbb-2', '2')
    _mod(d, 'older', 'bbbb-2', '1')
    D.adopt_manual_copies(d, SOURCES)
    assert sorted(os.listdir(d)) == ['.replaced', 'Folder Mod']
    assert _version(d, 'Folder Mod') == '2'
    assert sorted(os.listdir(os.path.join(d, '.replaced'))) == ['older',
                                                                 'stray']


def test_left_alone(tmp_path):
    d = str(tmp_path)
    _mod(d, 'Folder Mod', 'bbbb-2', '1')           # its own name
    _mod(d, 'Nitpick', 'aaaa-1', '1')               # the folder form of
    _mod(d, 'Something Else', 'zzzz-9', '1')        # ... and unsupported
    _mod(d, '.hidden', 'bbbb-2', '9')
    assert D.adopt_manual_copies(d, SOURCES) == []
    assert sorted(os.listdir(d)) == ['.hidden', 'Folder Mod', 'Nitpick',
                                     'Something Else']
