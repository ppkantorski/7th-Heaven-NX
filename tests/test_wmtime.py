"""BUILD 649: the world map's night and sunset looks on the day/night clock
(ff7nx_wmtime, native/wmtime.c). The blob is run in unicorn with a fake
guest page table.

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

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ff7nx_wmtime as WT                                     # noqa: E402

unicorn = pytest.importorskip('unicorn')
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM            # noqa: E402
from unicorn.arm64_const import (UC_ARM64_REG_X0, UC_ARM64_REG_X1,  # noqa: E402
                                 UC_ARM64_REG_SP, UC_ARM64_REG_LR,
                                 UC_ARM64_REG_CPACR_EL1)

CODE, ST, PT, HB, STK = 0x10000, 0x20000, 0x30000, 0x1000000, 0x40000


class Box:
    def __init__(self, hour=21, minute=0, on=1, maptype=0):
        self.uc = uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        uc.reg_write(UC_ARM64_REG_CPACR_EL1, 0x300000)
        for base, size in ((CODE, 0x10000), (ST, 0x1000), (PT, 0x10000),
                           (HB, 0x1000000), (STK, 0x10000)):
            uc.mem_map(base, size)
        uc.mem_write(CODE, open(WT.BLOB, 'rb').read())
        uc.mem_write(CODE + 0x8000, struct.pack('<I', 0xD65F03C0))   # ret
        uc.mem_write(PT, b''.join(struct.pack('<Q', HB + (i << 12))
                                  for i in range(0x1000)))
        self.fn = {k: CODE + v for k, v in WT.entries().items()}
        self.g(WT.MAP_TYPE, struct.pack('<I', maptype))
        self.g(WT.OPTIONS, bytes((on,)))
        self.g(WT.MINUTES, bytes((minute,)))
        self.g(WT.HOURS, bytes((hour,)))
        for i in range(16):
            e = bytearray(range(i, i + 0x24))
            e[0x13] = i & 1
            self.g(WT.ZONES + 0x24 * i, bytes(e))

    def g(self, va, data):
        self.uc.mem_write(HB + va, data)

    def gr(self, va, n):
        return bytes(self.uc.mem_read(HB + va, n))

    def call(self, name, *args):
        for r, v in zip((UC_ARM64_REG_X0, UC_ARM64_REG_X1), args):
            self.uc.reg_write(r, v)
        self.uc.reg_write(UC_ARM64_REG_SP, STK + 0x8000)
        self.uc.reg_write(UC_ARM64_REG_LR, CODE + 0x8000)
        self.uc.emu_start(self.fn[name], CODE + 0x8000, count=100000)


def test_blob_is_current():
    import hashlib
    src = os.path.join(os.path.dirname(WT.BLOB), 'wmtime.c')
    want = open(os.path.join(os.path.dirname(WT.BLOB),
                             'wmtime.src.sha256')).read().strip()
    assert hashlib.sha256(open(src, 'rb').read()).hexdigest() == want


def test_weights_crossfade_and_never_exceed_one():
    for m in range(1440):
        n, s = WT.weights(m)
        assert 0 <= n <= 256 and 0 <= s <= 256 and n + s <= 256, m
    assert WT.weights(3 * 60) == (256, 0)
    assert WT.weights(12 * 60) == (0, 0)
    assert WT.weights(7 * 60) == (0, 256)          # sunrise peak
    assert WT.weights(19 * 60) == (0, 256)         # sunset peak
    assert WT.weights(20 * 60) == (256, 0)
    for m in range(1439):                          # no jumps anywhere
        a, b = WT.weights(m), WT.weights(m + 1)
        assert abs(a[0] - b[0]) <= 16 and abs(a[1] - b[1]) <= 16, m


@pytest.mark.parametrize('minute', range(0, 1440, 7))
def test_zone_15_holds_the_clock_look_and_everything_is_restored(minute):
    b = Box(minute // 60, minute % 60)
    before = b.gr(WT.ZONES, 0x24 * 16)
    b.call('wt_pre', ST, PT)
    z = b.gr(WT.ZONES, 0x24 * 16)
    assert all(z[0x24 * i + 0x13] == 0 for i in range(15))     # towns muted
    q = z[0x24 * 15:]
    lk = WT.look(minute)
    if lk is None:
        assert q[0x13] == 0
    else:
        assert q[0x13] == 1
        assert (tuple(q[0x10:0x13]), tuple(q[0x14:0x17]), tuple(q[0x18:0x1B])) == lk
        assert struct.unpack_from('<II', q, 0x1C) == (1, 0x100000)
    b.call('wt_post', ST)
    assert b.gr(WT.ZONES, 0x24 * 16) == before


@pytest.mark.parametrize('kw', [dict(maptype=2), dict(maptype=3),
                                dict(hour=25)])
def test_nothing_changes_when_the_clock_does_not_apply(kw):
    b = Box(**kw)
    before = b.gr(WT.ZONES, 0x24 * 16)
    b.call('wt_pre', ST, PT)
    assert b.gr(WT.ZONES, 0x24 * 16) == before
    b.call('wt_post', ST)
    assert b.gr(WT.ZONES, 0x24 * 16) == before


def test_stub_calls_pre_target_post_in_order():
    import a64 as A
    fn = {'wt_pre': 0x1000, 'wt_post': 0x2000}
    w = WT.stub(0x3000, 0x4C1F900, fn)
    bls = [(0x3000 + 4 * i, x) for i, x in enumerate(w) if x >> 26 == 0x25]
    assert [x for _, x in bls] == [A.bl(0x3000 + 4 * i, t) for (i, t) in
                                   [((a - 0x3000) // 4, t) for (a, _), t in
                                    zip(bls, (0x1000, WT.TARGET, 0x2000))]]


@pytest.mark.parametrize('opt', [0x00, 0x04, 0xFE])
def test_the_savemap_options_bit_is_not_a_condition(opt):
    """BUILD 719: a save with bit 0 clear (made before the cycle, loaded on
    the world map) still gets the clock look; the byte is left alone."""
    for minute in (21 * 60, 18 * 60, 12 * 60):
        b = Box(minute // 60, minute % 60, on=opt)
        before = b.gr(WT.ZONES, 0x24 * 16)
        b.call('wt_pre', ST, PT)
        z = b.gr(WT.ZONES, 0x24 * 16)
        assert all(z[0x24 * i + 0x13] == 0 for i in range(15))
        lk = WT.look(minute)
        q = z[0x24 * 15:]
        if lk is None:
            assert q[0x13] == 0
        else:
            assert (tuple(q[0x10:0x13]), tuple(q[0x14:0x17]),
                    tuple(q[0x18:0x1B])) == lk
        assert b.gr(WT.OPTIONS, 1) == bytes((opt,))
        b.call('wt_post', ST)
        assert b.gr(WT.ZONES, 0x24 * 16) == before
