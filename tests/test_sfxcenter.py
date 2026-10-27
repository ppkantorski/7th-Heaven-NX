"""BUILD 738: Cosmo Memory's Center Battle SFX (external_sfx_always_centered).

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
import shutil
import struct
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import capstone                                                # noqa: E402
import iro                                                     # noqa: E402
import ff7nx_sfxcenter as C                                    # noqa: E402

MODULE = os.path.join(ROOT, 'dump', 'exefs', 'main')
need_dump = pytest.mark.skipif(not os.path.exists(MODULE),
                               reason='needs the dumped main')
MD = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)

# Cosmo Memory 1.21's own block, trimmed to the Center option.
MOD_XML = '''<ModInfo>
<ID>a5c2e6c1-0000-4000-8000-000000000001</ID>
<Name>Cosmo test</Name>
<ConfigOption>
  <Type>Bool</Type>
  <Default>1</Default>
  <ID>Center</ID>
  <Name>Center Battle SFX</Name>
  <Option Value="0" Name="Off"/>
  <Option Value="1" Name="On"/>
</ConfigOption>
<FFNxConfig>
  <use_external_sfx>true</use_external_sfx>
  <external_sfx_always_centered Center="1">true</external_sfx_always_centered>
</FFNxConfig>
</ModInfo>
'''


def dis(word):
    i = next(MD.disasm(struct.pack('<I', word), 0))
    return i.mnemonic + ' ' + i.op_str


def test_the_words():
    assert dis(C.STOCK) == 'mov w19, w1'
    assert dis(C.CENTRED) == 'mov w19, wzr'


def test_the_option_gates_the_flag(tmp_path):
    p = tmp_path / 'mod.xml'
    p.write_text(MOD_XML)
    m = iro.Manifest(str(p))
    assert m.ffnx_flags({})[C.FFNX_KEY] == 'true'           # default on
    assert m.ffnx_flags({'Center': 1})[C.FFNX_KEY] == 'true'
    assert m.ffnx_flags({'center': '1'})[C.FFNX_KEY] == 'true'
    assert C.FFNX_KEY not in m.ffnx_flags({'Center': 0})
    assert m.ffnx_flags({'Center': 0})['use_external_sfx'] == 'true'


def test_wanted_follows_the_flag_unless_forced():
    on = {C.FFNX_KEY: 'true'}
    assert C.wanted(on, env={})
    assert not C.wanted({}, env={})
    assert not C.wanted({C.FFNX_KEY: 'false'}, env={})
    assert not C.wanted(on, env={C.SFXCENTER_ENV: '0'})
    assert C.wanted({}, env={C.SFXCENTER_ENV: '1'})


@need_dump
def test_stock_module():
    t = C._text(MODULE)
    assert not C.check_anchors(t, log=lambda *_: None)
    assert C.state(t) == 'stock'


@need_dump
def test_apply_is_one_word_and_reverts(tmp_path):
    import nxmap
    out = tmp_path / 'main'
    shutil.copy(MODULE, out)
    before = nxmap.Main(str(out)).text
    assert C.apply(out, log=lambda *_: None) == 0
    after = nxmap.Main(str(out)).text
    diff = [va for va in range(0, len(before), 4)
            if before[va:va + 4] != after[va:va + 4]]
    assert diff == [C.SITE]
    assert C.state(after) == 'installed'
    assert C.apply(out, log=lambda *_: None) == 0           # idempotent
    assert C.apply(out, revert=True, log=lambda *_: None) == 0
    assert nxmap.Main(str(out)).text == before
