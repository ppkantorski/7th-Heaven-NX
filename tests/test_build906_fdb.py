"""
BUILD 906 -- ESUI "Field Dialog Box Transparency" (fdb) on the Switch.

Semi (Field/FieldSDB) and No Boxes (Field/FieldNDB) are x86 .text HEXT, so
every choice used to draw the stock box. Each now maps to its ARM words, from
the bytes the ACTIVE folders' HEXT write.
"""

import os as _nx_os, sys as _nx_sys  # find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import json
import os
import struct
import tempfile
from pathlib import Path

import pytest

import ff7nx_esui as E

ROOT = nxpaths.ROOT
MOD = os.path.join(ROOT, 'mods', 'Enhanced Stock UI-rel2')
MAIN = os.path.join(ROOT, 'dump', 'exefs', 'main')

EXPECT = {
    0: {},
    1: {0xD91B14: 0xD503201F},                       # cbz on [0xDC3CD8] -> nop
    2: {0xD991CC: 0xD503201F, 0xD991D4: 0x11003108},  # bl box -> nop, esp +0xC
}


@pytest.mark.skipif(not os.path.isdir(MOD), reason='ESUI mod not present')
@pytest.mark.parametrize('fdb', [0, 1, 2])
def test_906_each_choice_gets_its_words(fdb):
    import build
    m = build.Mod(MOD, tempfile.mkdtemp())
    m._load_manifest()
    o = dict(m.manifest.defaults())
    o['fdb'] = fdb
    E.CURRENT = None
    E._HEXT_MEM.clear()
    try:
        E.CURRENT = E.plan_for(m, o)
        got = {va: new for va, (_old, new) in E.fdb_words().items()}
        assert got == EXPECT[fdb]
    finally:
        E.CURRENT = None
        E._HEXT_MEM.clear()


@pytest.mark.skipif(not os.path.isfile(MAIN), reason='no dump main')
def test_906_stock_words_are_what_the_port_expects():
    import nso_patcher
    nso = nso_patcher.read_nso(Path(MAIN))
    text = next(g.data for g in nso.segments if g.name == '.text')
    for va, (old, _new) in {**E.FDB_SEMI_WORDS, **E.FDB_NONE_WORDS}.items():
        assert struct.unpack_from('<I', text, va)[0] == old, hex(va)


def test_906_default_is_semi():
    with open(os.path.join(ROOT, 'data', 'default_mod_settings.json')) as f:
        d = json.load(f)
    def find(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if 'Enhanced Stock UI' in str(k) and isinstance(v, dict):
                    return v
                hit = find(v)
                if hit is not None:
                    return hit
        return None
    esui = find(d)
    assert esui is not None
    assert esui['options']['fdb'] == 1
