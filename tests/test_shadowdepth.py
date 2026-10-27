"""BUILD 604/604b: ff7nx_shadowdepth -- world shadows carry the nearest
corner's 1/w, and a shadow's size stops at 0 high up.

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

import ff7nx_shadowdepth as S                 # noqa: E402

MODULE = os.path.join(ROOT, 'dump', 'exefs', 'main')
need_dump = pytest.mark.skipif(not os.path.exists(MODULE), reason='no dump')


def _dis(words, base=0x1000, addr=None):
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    cs = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    out = []
    for i, w in enumerate(words):
        a = addr(i) if addr else base + 4 * i
        ins = list(cs.disasm(struct.pack('<I', w), a))
        out.append(('%s %s' % (ins[0].mnemonic, ins[0].op_str)).strip())
    return out


def test_the_cave_disassembles_to_the_load():
    got = _dis(S.body(lambda i: 0x1000 + 4 * i))
    assert got == ['stp x29, x30, [sp, #-0x20]!', 'str x0, [sp, #0x10]',
                   'ldr w8, [x20, #0x14]', 'sub w0, w8, #0x160',
                   'bl #0x%x' % S.G2H, 'ldr w19, [x0]', 'ldr x0, [sp, #0x10]',
                   'ldp x29, x30, [sp], #0x20', 'ret']


def test_the_stock_word_is_mov_w19_one():
    assert _dis([S.STOCK_WORD]) == ['mov w19, #0x3f800000']


@need_dump
def test_the_site_in_the_dump():
    import nxmap
    m = nxmap.Main(MODULE)
    s, e = m.extent(0x75D544)
    assert s <= S.SITE < e
    assert S.read_state(m.img) == 'stock' and S._context_ok(m.img)
    # w19 is stored as the rhw of all four vertices and never rewritten
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    cs = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    stores, writes = 0, 0
    for i in cs.disasm(m.img[S.SITE + 4:e], S.SITE + 4):
        t = '%s %s' % (i.mnemonic, i.op_str)
        if t == 'str w19, [x0]':
            stores += 1
        elif t.split(' ')[0] not in ('str', 'stp', 'cmp', 'fcmp') and \
                t.split(' ')[1].rstrip(',') in ('w19', 'x19'):
            writes += 1
            break
        if stores == 4:
            break
    assert stores == 4 and writes == 0


@need_dump
def test_apply_on_the_dump_and_idempotent():
    import nxmap
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, 'main')
        assert S.apply_to_nso(MODULE, out)
        m = nxmap.Main(out)
        assert S.read_state(m.img) == 'on'
        a, b = nxmap.Main(MODULE).img, m.img
        changed = [o for o in range(0, min(len(a), len(b)), 4)
                   if a[o:o + 4] != b[o:o + 4] and a[o:o + 4] != b'\0\0\0\0']
        assert changed == sorted([S.SITE, S.SIZE_SITE])
        assert S.depth_state(m.img) == 'on' and S.size_state(m.img) == 'on'
        assert not S.apply_to_nso(out, out + '.2')


def _size_rule(h):
    """What the patched setup computes: max(0, 0x64 - (h >> 6))."""
    return max(0, 0x64 - (h >> 6))


def test_the_size_cave_and_the_rule():
    assert _dis(S.size_body()) == ['sub w20, w9, w8', 'cmp w20, #0',
                                   'csel w20, w20, wzr, gt', 'ret']
    assert _dis([S.SIZE_STOCK]) == ['sub w20, w9, w8']
    assert _size_rule(0) == 100 and _size_rule(3200) == 50
    assert _size_rule(6399) == 1 and _size_rule(6400) == 0
    assert _size_rule(20000) == 0 and _size_rule(30767) == 0


@need_dump
def test_the_size_site_in_the_dump():
    import nxmap
    m = nxmap.Main(MODULE)
    s, e = m.extent(0x75DEAA)
    assert s <= S.SIZE_SITE < e and S._size_context_ok(m.img)
    assert S.size_state(m.img) == 'stock'


@need_dump
def test_a_604_main_gets_only_the_size_floor():
    import nxmap
    with tempfile.TemporaryDirectory() as tmp:
        d604 = os.path.join(tmp, 'm604')
        img = nxmap.Main(MODULE).img
        words = S.build_patches(img, set(nxmap.Main(MODULE).arm_starts))
        # a 604 main: only the depth site and its cave
        full = os.path.join(tmp, 'full')
        assert S.apply_to_nso(MODULE, full)
        import nso_patcher
        from pathlib import Path
        del words
        m = nxmap.Main(full)
        assert S.read_state(m.img) == 'on'
        assert S.read_state(img) == 'stock'
        # depth-only: put the size site back to stock in a copy
        nso = nso_patcher.read_nso(Path(full))
        hx = lambda v: ' '.join('%02X' % b for b in struct.pack('<I', v))
        nso_patcher.apply_spec(nso, {'name': 't', 'patches': [{
            'name': 'x', 'va': hex(S.SIZE_SITE),
            'expect': hx(struct.unpack_from('<I', m.img, S.SIZE_SITE)[0]),
            'set': hx(S.SIZE_STOCK)}]})
        open(d604, 'wb').write(nso_patcher.rebuild(nso))
        assert S.read_state(nxmap.Main(d604).img) == 'depth-only'
        out = os.path.join(tmp, 'out')
        assert S.apply_to_nso(d604, out)
        a, b = nxmap.Main(d604).img, nxmap.Main(out).img
        assert S.read_state(b) == 'on'
        assert struct.unpack_from('<I', b, S.SITE)[0] == struct.unpack_from('<I', a, S.SITE)[0]


def test_off_switch():
    assert S.enabled({})
    assert not S.enabled({S.ENV: '0'})
