"""BUILD 603: ff7nx_texscale -- the scaler's filtered copy capped at 1024 px.

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
import struct
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import ff7nx_texscale as T                    # noqa: E402

MODULE = os.path.join(ROOT, 'dump', 'exefs', 'main')
need_dump = pytest.mark.skipif(not os.path.exists(MODULE), reason='no dump')


def test_the_caves_disassemble_to_the_rule():
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    cs = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    want_tail = ['mov w5, #4', 'cmp w16, #0x100', 'mov w17, #2',
                 'csel w5, w5, w17, ls', 'cmp w16, #0x200', 'mov w17, #1',
                 'csel w5, w5, w17, ls', 'ret']
    heads = (['cmp w21, w20', 'csel w16, w21, w20, hi'],
             ['cmp w2, w3', 'csel w16, w2, w3, hi'])
    for (_site, pick, _what), head in zip(T.SITES, heads):
        code = b''.join(struct.pack('<I', w) for w in T.body(pick))
        got = [('%s %s' % (i.mnemonic, i.op_str)).strip() for i in cs.disasm(code, 0)]
        assert got == head + want_tail


def test_the_scale_rule():
    table = {(32, 32): 4, (256, 256): 4, (250, 250): 4, (257, 64): 2,
             (512, 512): 2, (64, 512): 2, (513, 8): 1, (768, 768): 1,
             (1024, 512): 1, (640, 480): 1}
    for (w, h), s in table.items():
        assert T.scale_of(w, h) == s
        assert max(w, h) * s <= max(1024, max(w, h))   # never over 1024 unless 1x


def test_the_sites_are_the_stock_mov_w5_4():
    assert T.STOCK_WORD == 0x321E03E5
    assert [s for s, _p, _w in T.SITES] == [0x10DCB34, 0x10D7270]


@need_dump
def test_apply_on_the_dump_places_both_caves_and_is_idempotent():
    import nxmap
    assert T.read_state(nxmap.Main(MODULE).img) == 'stock'
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, 'main')
        assert T.apply_to_nso(MODULE, out)
        m = nxmap.Main(out)
        assert T.read_state(m.img) == 'capped'
        for site, pick, _what in T.SITES:
            w = struct.unpack_from('<I', m.img, site)[0]
            tgt = T._bl_target(site, w)
            assert tgt is not None and T._walk(m.img, tgt, 10) == T.body(pick)
        # only the two sites changed among the non-zero words
        a, b = nxmap.Main(MODULE).img, m.img
        changed = [o for o in range(0, min(len(a), len(b)), 4)
                   if a[o:o + 4] != b[o:o + 4] and a[o:o + 4] != b'\0\0\0\0']
        assert sorted(changed) == sorted(s for s, _p, _w in T.SITES)
        assert not T.apply_to_nso(out, out + '.2')


def test_off_switch():
    assert T.enabled({})
    assert not T.enabled({T.ENV: '0'})
