"""tests/test_pccode.py -- PC x86 code option folders are left out whole.

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
import tempfile

import build
import ff7nx_pccode

MOD_XML = """<?xml version="1.0"?>
<ModInfo>
  <ID>11111111-2222-3333-4444-555555555555</ID>
  <Name>Code Tweaks</Name>
  <ModFolder Folder="Code" ActiveWhen="Code = 1" />
  <ModFolder Folder="Code\\Script" ActiveWhen="Code = 1" />
  <ModFolder Folder="Cave" ActiveWhen="Cave = 1" />
  <ModFolder Folder="Prices" ActiveWhen="Prices = 1" />
  <ModFolder Folder="Art" ActiveWhen="Art = 1" />
  <ModFolder Folder="Misc\\WItemFix" ActiveWhen="WItem = 1" />
  <ConfigOption><Type>Bool</Type><Default>0</Default><ID>Code</ID><Name>Code</Name>
    <Option Value="0" Name=""/><Option Value="1" Name=""/></ConfigOption>
  <ConfigOption><Type>Bool</Type><Default>0</Default><ID>Cave</ID><Name>Cave</Name>
    <Option Value="0" Name=""/><Option Value="1" Name=""/></ConfigOption>
  <ConfigOption><Type>Bool</Type><Default>0</Default><ID>Prices</ID><Name>Prices</Name>
    <Option Value="0" Name=""/><Option Value="1" Name=""/></ConfigOption>
  <ConfigOption><Type>Bool</Type><Default>0</Default><ID>Art</ID><Name>Art</Name>
    <Option Value="0" Name=""/><Option Value="1" Name=""/></ConfigOption>
  <ConfigOption><Type>Bool</Type><Default>0</Default><ID>WItem</ID><Name>WItem</Name>
    <Option Value="0" Name=""/><Option Value="1" Name=""/></ConfigOption>
</ModInfo>
"""

FILES = {
    # .text hook + a code cave in .data + a kernel the code reads
    'Code/hext/a.txt': '00500000 = E8 00 00 00 00\n009A7830 = 90 90 90 90\n',
    'Code/kernel/KERNEL.BIN': 'k',
    'Code/Code.png': 'preview',
    'Code/Script/flevel.lgp/md1stin.chunk.1': 'c',
    # data-only, but inside Code's cave
    'Cave/hext/b.txt': '009A7832 = 05\n',
    # data-only, its own table: stays
    'Prices/hext/c.txt': '00924E60 = E8 03 00 00\n',
    # code HEXT next to real content: stays (content mods are not judged)
    'Art/hext/d.txt': '00500010 = 90\n',
    'Art/field/char.lgp/aaaa.tex': 't',
    'Misc/WItemFix/hext/e.txt': '00500020 = 90\n',
}


def _mod():
    root = tempfile.mkdtemp()
    path = os.path.join(root, 'CodeTweaks')
    os.makedirs(path)
    with open(os.path.join(path, 'mod.xml'), 'w') as f:
        f.write(MOD_XML)
    for rel, body in FILES.items():
        full = os.path.join(path, *rel.split('/'))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w') as f:
            f.write(body)
    m = build.Mod(path, os.path.join(root, 'cache'))
    m._load_manifest()
    return m


def test_folders():
    m = _mod()
    gone = ff7nx_pccode.analyse(m)
    assert set(gone) == {'code', 'code/script', 'cave', 'misc/witemfix'}, gone
    assert gone['misc/witemfix'].startswith('Built in')


def test_options_locked():
    m = _mod()
    assert ff7nx_pccode.locked_option(m, 'Code')
    assert ff7nx_pccode.locked_option(m, 'Cave')
    assert ff7nx_pccode.locked_option(m, 'WItem').startswith('Built in')
    assert ff7nx_pccode.locked_option(m, 'Prices') is None
    assert ff7nx_pccode.locked_option(m, 'Art') is None


def test_files_for_drops_whole_folder():
    m = _mod()
    on = {'Code': 1, 'Cave': 1, 'Prices': 1, 'Art': 1, 'WItem': 1}
    rels = {r.replace(os.sep, '/') for r, _f, _o in m.files_for(on)}
    assert not any(r.startswith(('Code/', 'Cave/', 'Misc/')) for r in rels), rels
    assert 'Prices/hext/c.txt' in rels and 'Art/hext/d.txt' in rels


def test_override():
    m = _mod()
    os.environ[ff7nx_pccode.ENV] = 'apply'
    try:
        assert ff7nx_pccode.analyse(m) == {}
    finally:
        del os.environ[ff7nx_pccode.ENV]


if __name__ == '__main__':
    for name, fn in sorted(globals().items()):
        if name.startswith('test_'):
            fn()
            print('ok', name)
