"""BUILD 860h: the field depth copy draws with no colour target
(ff7nx_depthcopy).

The one word is checked against the stock dump, the device's SetRenderTargets
is run in unicorn to show that a NULL array unbinds colour targets 0..3 while
an array of nulls leaves them bound (the bug), and the module is applied.

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

import pytest

import a64 as A                                                # noqa: E402
import ff7nx_depthcopy as D                                    # noqa: E402

DUMP = os.path.join(nxpaths.ROOT, 'dump', 'exefs', 'main')
needs_dump = pytest.mark.skipif(not os.path.isfile(DUMP),
                                reason='no exefs/main dump')


def test_words():
    assert D.FIX_WORD == A.mov_reg64(2, 31)
    assert D.STOCK_WORD == A.sub_imm64(2, 29, 0x50)


@needs_dump
def test_stock_dump_is_stock():
    import nxmap
    assert D.state(nxmap.Main(DUMP).img) == 'stock'


def _set_render_targets(rts_null):
    """Run the device's SetRenderTargets (+0x1137540) with a fake device whose
    vtable +0x160 (SetRenderTarget) records (index, surface) and +0x168
    (SetDepthStencil) records the surface. Returns the recorded calls."""
    unicorn = pytest.importorskip('unicorn')
    from unicorn import arm64_const as R
    import nxmap
    m = nxmap.Main(DUMP)
    uc = unicorn.Uc(unicorn.UC_ARCH_ARM64, unicorn.UC_MODE_ARM)
    uc.mem_map(0x1137000, 0x1000)
    uc.mem_write(0x1137000, m.img[0x1137000:0x1138000])
    DEV, VT, STK, REC, ARR = 0x2000000, 0x2001000, 0x3000000, 0x4000000, 0x5000000
    for b in (DEV, VT, STK, REC, ARR, 0x6000000):
        uc.mem_map(b, 0x1000)
    uc.mem_write(DEV, struct.pack('<Q', VT))
    # +0x160: rec[n] = (w1, x2); +0x168: rec depth at REC + 0x800
    srt = [A.movz(9, REC >> 16) | (1 << 21), A.ldr(10, 9, 0x7F0),
           A.add_reg64_lsl(11, 9, 10, 4),
           A.str64(1, 11, 0),
           A.str64(2, 11, 8), A.add_imm(10, 10, 1), A.str_(10, 9, 0x7F0),
           0xD65F03C0]
    sds = [A.movz(9, REC >> 16) | (1 << 21), A.str64(1, 9, 0x800), 0xD65F03C0]
    uc.mem_write(0x6000000, b''.join(struct.pack('<I', w) for w in srt))
    uc.mem_write(0x6000100, b''.join(struct.pack('<I', w) for w in sds))
    uc.mem_write(VT + 0x160, struct.pack('<Q', 0x6000000))
    uc.mem_write(VT + 0x168, struct.pack('<Q', 0x6000100))
    uc.mem_write(ARR, struct.pack('<QQ', 0, 0))
    uc.reg_write(R.UC_ARM64_REG_X0, DEV)
    uc.reg_write(R.UC_ARM64_REG_X1, 2)
    uc.reg_write(R.UC_ARM64_REG_X2, 0 if rts_null else ARR)
    uc.reg_write(R.UC_ARM64_REG_X3, 0xDE97)
    uc.reg_write(R.UC_ARM64_REG_SP, STK + 0x800)
    uc.reg_write(R.UC_ARM64_REG_X30, 0x6000200)
    uc.mem_write(0x6000200, struct.pack('<I', 0xD65F03C0))
    uc.emu_start(0x1137540, 0x6000200, count=500)
    n = struct.unpack('<I', bytes(uc.mem_read(REC + 0x7F0, 4)))[0]
    calls = [struct.unpack('<QQ', bytes(uc.mem_read(REC + 16 * i, 16)))
             for i in range(n)]
    depth = struct.unpack('<Q', bytes(uc.mem_read(REC + 0x800, 8)))[0]
    return calls, depth


@needs_dump
def test_null_array_unbinds_colour_targets():
    calls, depth = _set_render_targets(rts_null=True)
    assert depth == 0xDE97
    assert sorted(i for i, s in calls) == [0, 1, 2, 3]
    assert all(s == 0 for i, s in calls)


@needs_dump
def test_array_of_nulls_leaves_them_bound():
    calls, depth = _set_render_targets(rts_null=False)
    assert depth == 0xDE97
    assert calls == []                           # the bug: nothing unbound


def test_env():
    assert D.enabled({}) and not D.enabled({D.ENV: '0'})


@needs_dump
def test_apply(tmp_path):
    import nxmap
    out = tmp_path / 'main'
    assert D.apply_to_nso(DUMP, str(out), log=lambda *_: None)
    assert D.state(nxmap.Main(str(out)).img) == 'fixed'
    assert not D.apply_to_nso(str(out), str(tmp_path / 'again'),
                              log=lambda *_: None)
