"""BUILD 541: snowboard textures keep the PSX colour-key contract.

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

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import build  # noqa: E402
import lgp  # noqa: E402
import tex  # noqa: E402

VAN = os.path.join(HERE, 'dump', 'romfs', 'ff7', 'workingdir', 'data',
                   'minigame', 'snowboard-us.lgp')


def test_vanilla_is_a_fixed_point():
    a = lgp.Archive(VAN)
    for n in a.names():
        if n.endswith('.tex'):
            d = a.index[n]['payload']
            assert build.psx_contract_palette(d, d) is None, n


def test_breaks_are_repaired_and_indices_kept():
    a = lgp.Archive(VAN)
    d = a.index['waku.tex']['payload']
    t = tex.parse(d)
    pal = bytearray(t['palette'])
    pal[0:4] = bytes((151, 151, 151, 0))        # de-fringed entry 0
    pal[4:8] = bytes((0, 0, 0, 255))            # opaque black
    off = len(d) - len(t['pixels']) - len(pal)
    bad = d[:off] + bytes(pal) + d[off + len(pal):]
    new = build.psx_contract_palette(d, bad)
    assert new is not None and tex.check_indices_unchanged(bad, new)
    p = tex.parse(new)['palette']
    assert p[0:4] == bytes((0, 0, 0, 0))
    assert p[4:7] == bytes((8, 8, 8)) and p[7] == 254   # vanilla STP alpha


def test_scope():
    assert 'snowboard-us.lgp' in build.PSX_KEYED_ARCHIVES
    assert 'chocobo.lgp' not in build.PSX_KEYED_ARCHIVES
