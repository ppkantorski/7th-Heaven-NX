"""BUILD 607: ff7nx_glstream -- the per-draw glBufferData uploads pass a
streaming usage hint instead of GL_STATIC_DRAW.

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
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import ff7nx_glstream as G                    # noqa: E402

MODULE = os.path.join(ROOT, 'dump', 'exefs', 'main')
need_dump = pytest.mark.skipif(not os.path.exists(MODULE), reason='no dump')


def _dis(w):
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    ins = list(Cs(CS_ARCH_ARM64, CS_MODE_ARM).disasm(struct.pack('<I', w), 0x1000))
    return ('%s %s' % (ins[0].mnemonic, ins[0].op_str)).strip()


def test_the_words():
    assert _dis(G.STOCK) == 'mov w3, #0x88e4'
    assert _dis(G.mov_w3(G.HINTS['stream'])) == 'mov w3, #0x88e0'
    assert _dis(G.mov_w3(G.HINTS['dynamic'])) == 'mov w3, #0x88e8'
    assert G.mov_w3(0x88E4) == G.STOCK


def test_the_env():
    assert G.mode({}) == 'stream'
    assert G.mode({G.ENV: '0'}) is None
    assert G.mode({G.ENV: 'dynamic'}) == 'dynamic'


@need_dump
def test_the_dump_is_stock_and_these_are_the_only_uploads():
    import nxmap
    import nsosyms
    img = nxmap.Main(MODULE).img
    assert G.read_state(img) == 'stock'
    _m, plt = nsosyms.imports(MODULE)
    assert plt[G.GL_BUFFER_DATA] == 'glBufferData'
    calls = []
    for va in range(0, len(img) - 4, 4):
        if G._bl_target(va, struct.unpack_from('<I', img, va)[0]) == G.GL_BUFFER_DATA:
            calls.append(va)
    assert calls == sorted(s + 12 for s in G.SITES)


@need_dump
@pytest.mark.parametrize('which', ['stream', 'dynamic'])
def test_apply_changes_three_words_only(which):
    import nxmap
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, 'main')
        assert G.apply_to_nso(MODULE, out, which=which)
        a, b = nxmap.Main(MODULE).img, nxmap.Main(out).img
        assert len(a) == len(b)
        diff = [va for va in range(0, len(a), 4) if a[va:va + 4] != b[va:va + 4]]
        assert diff == sorted(G.SITES)
        assert G.read_state(b) == which
        assert not G.apply_to_nso(out, os.path.join(d, 'again'), which=which)


@need_dump
def test_cli_on_off_restores_the_bytes():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, 'main')
        shutil.copyfile(MODULE, p)
        assert G.main(['x', p, '--on']) == 0
        assert G.main(['x', p, '--on']) == 1
        assert G.main(['x', p, '--off']) == 0
        assert open(p, 'rb').read() == open(MODULE, 'rb').read()
