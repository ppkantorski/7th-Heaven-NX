"""BUILD 665: a field's sound effects decoded at field entry
(ff7nx_sfxpreload, native/sfxpre.c). The blob runs in unicorn against a fake
guest page table; the two hook stubs run in arm64emu with the stock functions
modelled.

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
import hashlib
import os
import struct
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import a64 as A                                               # noqa: E402
import arm64emu as E                                          # noqa: E402
import ff7nx_sfxpreload as S                                  # noqa: E402

unicorn = pytest.importorskip('unicorn')
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM            # noqa: E402
from unicorn.arm64_const import (UC_ARM64_REG_X0, UC_ARM64_REG_X1,  # noqa: E402
                                 UC_ARM64_REG_X2, UC_ARM64_REG_X3,
                                 UC_ARM64_REG_SP,
                                 UC_ARM64_REG_LR, UC_ARM64_REG_CPACR_EL1)

CODE, ST, TAB, PT, STK, CTX = 0x10000, 0x20000, 0x21000, 0x30000, 0x40000, 0x50000
HB = 0x1000000                    # guest page p lives at HB + p * 0x1000
LP = 0x00A00000                   # guest address of the field file
EBP = 0x00F00F00


def section1(ops, ne=2, na=1):
    """A section 1 whose code is `ops` (bytes)."""
    code_start = 32 + 8 * ne + 4 * na + 64 * ne
    body = bytearray(code_start) + ops + b'\x00'
    struct.pack_into('<HBBHHH', body, 0, 0x0502, ne, 0, len(body) - 1, na, 512)
    return bytes(body)


def se(i):
    return bytes((0xF1, 0x00)) + struct.pack('<H', i) + b'\x40'


def akao(cmd, i, op=0xF2):
    if op == 0xF2:
        return bytes((0xF2, 0, 0, 0, cmd, 0x40)) + struct.pack('<H', i) + bytes(6)
    return bytes((0xDA, 0, 0, 0, cmd, 0x40, 0)) + struct.pack('<H', i) + bytes(6)


class Box:
    def __init__(self, ops, routes=None, cap=128 << 10, budget=1536 << 10,
                 meta=None, foot=None, cmfs=None):
        self.uc = uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        uc.reg_write(UC_ARM64_REG_CPACR_EL1, 0x300000)
        for base, size in ((CODE, 0x10000), (ST, 0x1000), (TAB, 0x1000),
                           (PT, 0x8000), (STK, 0x10000), (CTX, 0x1000),
                           (HB, 0x1000000)):
            uc.mem_map(base, size)
        uc.mem_write(CODE, open(S.BLOB, 'rb').read())
        uc.mem_write(CODE + 0xF000, struct.pack('<I', 0xD65F03C0))
        # guest pages 0..0xFFF map to HB.. (only the pages a test touches
        # exist; the rest are NULL in the table, like an unmapped page)
        uc.mem_write(PT, b''.join(struct.pack('<Q', HB + ((i & 0xFFF) << 12))
                                  for i in range(0x1000)))
        self.fn = {k: CODE + v for k, v in S.entries().items()}
        uc.mem_write(TAB, S.table(routes or {}, cap, budget, foot))
        self.meta = meta or {}
        self.cmfs = cmfs
        for i in range(1, 751):
            ln, loop = self.meta.get(i, (0x4000, 0))
            self.g(0xDE0E84 + (i - 1) * 0x1C,
                   struct.pack('<IIB', ln, 0x100000 + i * 0x10000, loop))
        self.set_field(ops, 0x41)

    def set_field(self, ops, fid, lp=LP, cmfs=None):
        """Sections 1..9 (2..9 empty), then optionally the stock 'FINAL
        FANTASY7' tail and a CMFS trailer of (base, count, cursor) records:
        cmfs = (fallback, [triangle records])."""
        s1 = section1(ops)
        f = bytearray(64)
        offs = []
        f += struct.pack('<I', len(s1)) + s1
        offs.append(64)
        for _ in range(8):
            offs.append(len(f))
            f += struct.pack('<I', 0)
        for k, o in enumerate(offs):
            struct.pack_into('<I', f, 6 + 4 * k, o)
        cmfs = cmfs if cmfs is not None else getattr(self, 'cmfs', None)
        if cmfs is not None:
            fb, tris = cmfs
            f += b'FINAL FANTASY7'
            f += struct.pack('<IH', 0x53464D43, len(tris)) + bytes(fb)
            for r in tris:
                f += bytes(r)
        self.g(lp, bytes(f))
        self.g(0xCFF594, struct.pack('<I', lp))
        self.g(0xCFF468, struct.pack('<H', fid))

    def g(self, va, data):
        self.uc.mem_write(HB + (va & 0xFFFFFF), data)

    def gr(self, va, n):
        return bytes(self.uc.mem_read(HB + (va & 0xFFFFFF), n))

    def gu(self, va):
        return struct.unpack('<I', self.gr(va, 4))[0]

    def call(self, name, *args):
        for r, v in zip((UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2,
                         UC_ARM64_REG_X3), args):
            self.uc.reg_write(r, v)
        self.uc.reg_write(UC_ARM64_REG_SP, STK + 0x8000)
        self.uc.reg_write(UC_ARM64_REG_LR, CODE + 0xF000)
        self.uc.emu_start(self.fn[name], CODE + 0xF000, count=2000000)
        return self.uc.reg_read(UC_ARM64_REG_X0) & 0xFFFFFFFF

    def st(self, off, n=1):
        return struct.unpack_from('<%dI' % n, self.uc.mem_read(ST + off, 4 * n))

    def plan(self):
        return self.call('sp_plan', ST, PT, TAB)

    def lists(self):
        nrel, nload = self.st(S.ST_NREL, 2)
        return (list(self.st(S.ST_REL, nrel)) if nrel else [],
                [i for i in self.st(S.ST_LOAD, nload) if i != 159]
                if nload else [])

    def loads(self):
        nload = self.st(S.ST_NLOAD)[0]
        return list(zip(self.st(S.ST_LOAD, nload), self.st(S.ST_LOADOFF, nload)))

    def load(self, i, buf):
        """What sfx_load(i, 0) does: the buffer lands in the cache."""
        self.g(0xDE0128 + (i - 1) * 4, struct.pack('<I', buf))
        return self.call('sp_take', ST, PT, i)


def test_blob_is_current():
    src = os.path.join(HERE, 'native', 'sfxpre.c')
    want = open(os.path.join(HERE, 'native', 'sfxpre.src.sha256')).read().strip()
    assert hashlib.sha256(open(src, 'rb').read()).hexdigest() == want


def test_tables_size_the_sound_opcodes():
    ops, spc = S.op_tables()
    assert (ops[0xF1], ops[0xF2], ops[0xDA]) == (5, 14, 15)
    assert ops[0x0F] == ops[0x28] == 0
    assert len(S.table({}, 1, 1)) == S.TAB_BYTES


def test_plan_collects_literal_sounds_once_and_skips_what_it_must():
    ops = (se(16) + akao(0x20, 49) + akao(0x2A, 193, 0xDA) + se(16)
           + akao(0x40, 77)               # not a play command
           + bytes((0xF1, 0x01, 5, 0, 0x40))   # id from a variable bank
           + se(359) + se(740)            # 740: reserved row
           + se(20) + se(30) + se(0))
    b = Box(ops, meta={20: (0x4000, 1),          # looping
                       30: (300 << 10, 0)})      # over the cap
    assert b.plan() == 5                           # + the stock step row 159
    assert b.lists() == ([], [16, 49, 193, 359])
    assert b.plan() == 0                         # same field: nothing more


def test_a_rotating_row_loads_once_per_variant_and_keys_by_offset():
    b = Box(se(25) + se(64), routes={25: 3})
    b.plan()
    assert b.lists()[1] == [25, 25, 25, 64]
    for k, buf in enumerate((0x111, 0x222, 0x333)):
        b.g(0xDE0E84 + 24 * 0x1C + 4, struct.pack('<I', 0x900000 + k * 0x1000))
        assert b.load(25, buf) == 0
        assert b.gu(0xDE0128 + 24 * 4) == 0         # cache left empty
    assert b.load(64, 0x444) == 0
    # the same variant a second time is handed back for release
    assert b.load(25, 0x555) == 0x555
    assert b.st(S.ST_NENT)[0] == 4
    # sfx_load for row 25 with the bridge on variant 1 finds variant 1's copy
    b.g(0xDE0E84 + 24 * 0x1C + 4, struct.pack('<I', 0x901000))
    b.g(EBP - 4, struct.pack('<I', 24))
    ctx = struct.pack('<8I', 0, 0, 0, 0, EBP - 0x20, EBP, 0, 0)
    b.uc.mem_write(CTX, ctx)
    assert b.call('sp_find', ST, PT, CTX) == 0x222
    b.g(0xDE0E84 + 24 * 0x1C + 4, struct.pack('<I', 0x123000))
    assert b.call('sp_find', ST, PT, CTX) == 0      # unknown variant: stock


def test_store_puts_the_copy_where_sfx_load_would():
    b = Box(se(16))
    b.g(EBP - 4, struct.pack('<I', 15))
    for out, where in ((0, 0xDE0128 + 15 * 4), (0xE00440, 0xE00440)):
        b.g(EBP + 12, struct.pack('<I', out))
        b.uc.mem_write(CTX, struct.pack('<8I', 0xABC, 0, 0, 0, 0, EBP, 0, 0))
        b.call('sp_store', ST, PT, CTX)
        assert b.gu(where) == 0xABC
        assert struct.unpack('<I', b.uc.mem_read(CTX, 4))[0] == 1


def test_next_field_releases_what_it_does_not_use_and_keeps_the_rest():
    b = Box(se(16) + se(49))
    b.plan()
    b.load(16, 0x1600)
    b.load(49, 0x4900)
    b.set_field(se(49) + se(193), 0x42)
    assert b.plan() == 3                           # 159 not held: + 1
    assert b.lists() == ([0x1600], [193])
    assert b.st(S.ST_NENT)[0] == 1


def test_a_sound_the_game_already_cached_is_left_to_the_game():
    b = Box(se(16) + se(49))
    b.g(0xDE0128 + 15 * 4, struct.pack('<I', 0x7777))
    b.plan()
    assert b.lists()[1] == [49]


def test_the_budget_and_the_table_size_bound_what_is_held():
    ops = b''.join(se(i) for i in range(100, 160))
    b = Box(ops, budget=64 << 10)                 # 0x4000 each -> 4 fit
    b.plan()
    assert b.lists()[1] == [100, 101, 102, 103]
    b = Box(ops, budget=8 << 20)
    b.plan()
    assert len(b.lists()[1]) == 48


# ------------------------------------------------------------ the stubs
GOT_PAGES, GOT_CTX = S.GUEST_PAGES_GOT, S.CTX_GOT
STATE = 0x4C21B20
FN = {'sp_plan': 0x99F000, 'sp_take': 0x99F100, 'sp_find': 0x99F200,
      'sp_store': 0x99F300, 'sp_prep': 0x99F400}


class Rig:
    def __init__(self):
        self.mem = E.Mem()
        self.cpu = E.Cpu(self.mem, native={})
        self.ctx = 0x8000000
        self.mem.setu(GOT_CTX, self.ctx, 8)
        self.mem.setu(GOT_PAGES, 0xABCD000, 8)
        self.esp = 0x7FF00
        self.mem.setu(self.ctx + 0x10, self.esp, 4)
        for k, v in enumerate((0x11, 0x22, 0x33, 0x44, self.esp, 0x66, 0x77, 0x88)):
            if k != 4:
                self.mem.setu(self.ctx + 4 * k, v, 4)
        self.mem.setu(self.ctx + 0x20, 0x0102030405060708, 8)
        self.mem.setu(self.ctx + 0x28, 0x1112131415161718, 8)
        self.calls = []

    def guest(self, va, n=4):
        return self.mem.u(self.cpu.guest_to_host(va), n)

    def cdecl(self, name, nargs, effect=None):
        def f(cpu):
            esp = self.mem.u(self.ctx + 0x10, 4)
            assert esp & 15 == 0 and esp <= self.esp - 4 * (nargs + 1)
            args = [self.guest(esp + 4 + 4 * i) for i in range(nargs)]
            self.calls.append((name,) + tuple(args))
            self.mem.setu(self.ctx + 0x10, esp + 4, 4)   # callee's ret
            for r in list(range(0, 19)) + [30]:
                cpu.x[r] = 0xDEAD0000DEAD
            self.mem.setu(self.ctx, effect(args) if effect else 0, 4)
            self.mem.setu(self.ctx + 4, 0xEEEE, 4)
        return f


def test_site_stub_releases_loads_takes_and_tail_calls_the_target():
    r = Rig()
    at = 0x99E000
    w = S.site_stub(at, STATE, 0x99F800, FN)
    st = r.cpu
    plan_state = {'n': 0}

    def sp_plan(cpu):
        assert cpu.x[0] == STATE and cpu.x[1] == 0xABCD000 and cpu.x[2] == 0x99F800
        r.mem.setu(STATE + S.ST_NREL, 1, 4)
        r.mem.setu(STATE + S.ST_NLOAD, 2, 4)
        r.mem.setu(STATE + S.ST_REL, 0x5150, 4)
        r.mem.setu(STATE + S.ST_LOAD, 16, 4)
        r.mem.setu(STATE + S.ST_LOAD + 4, 49, 4)
        cpu.x[0] = 3

    def sp_take(cpu):
        r.calls.append(('take', cpu.x[2] & 0xFFFFFFFF))
        cpu.x[0] = 0xB0B if cpu.x[2] == 49 else 0
    def sp_prep(cpu):
        assert cpu.x[0] == STATE and cpu.x[2] == 0x99F800
        r.calls.append(('prep', cpu.x[3] & 0xFFFFFFFF))
    st.native = {FN['sp_plan']: sp_plan, FN['sp_take']: sp_take,
                 FN['sp_prep']: sp_prep,
                 S.RELEASE: r.cdecl('release', 1),
                 S.SFX_LOAD: r.cdecl('load', 2, lambda a: 1)}
    st.sp = 0x7000000
    for reg in range(19, 25):
        st.x[reg] = 0x1900 + reg
    st.x[30] = A_RET = S.SITE + 4
    out = st.run(at, w)
    assert out == S.TARGET
    assert st.x[30] == A_RET and st.sp == 0x7000000
    assert all(st.x[reg] == 0x1900 + reg for reg in range(19, 25))
    assert r.calls == [('release', 0x5150), ('prep', 0), ('load', 16, 0),
                       ('take', 16), ('prep', 1), ('load', 49, 0),
                       ('take', 49), ('release', 0xB0B)]
    assert r.mem.u(r.ctx + 0x10, 4) == r.esp
    assert [r.mem.u(r.ctx + 4 * k, 4) for k in (0, 1, 2, 3, 5, 6, 7)] == \
        [0x11, 0x22, 0x33, 0x44, 0x66, 0x77, 0x88]
    assert r.mem.u(r.ctx + 0x20, 8) == 0x0102030405060708
    assert r.mem.u(r.ctx + 0x28, 8) == 0x1112131415161718


def test_site_stub_is_just_the_call_when_there_is_nothing_to_do():
    r = Rig()
    w = S.site_stub(0x99E000, STATE, 0x99F800, FN)

    def sp_plan(cpu):
        cpu.x[0] = 0
    r.cpu.native = {FN['sp_plan']: sp_plan}
    r.cpu.sp = 0x7000000
    r.cpu.x[30] = S.SITE + 4
    assert r.cpu.run(0x99E000, w) == S.TARGET
    assert r.cpu.x[30] == S.SITE + 4 and r.cpu.sp == 0x7000000


@pytest.mark.parametrize('held,dup', [(0, 0), (0x4444, 0x5555), (0x4444, 0)])
def test_miss_cave(held, dup):
    r = Rig()
    w = S.miss_cave(0x99E800, STATE, FN)
    stored = []

    def sp_find(cpu):
        assert cpu.x[2] == r.ctx
        cpu.x[0] = held

    def sp_store(cpu):
        stored.append(r.mem.u(r.ctx, 4))
    r.cpu.native = {FN['sp_find']: sp_find, FN['sp_store']: sp_store,
                    S.DUPLICATE: r.cdecl('dup', 1, lambda a: dup)}
    r.cpu.x[20] = r.ctx
    r.cpu.x[21] = 0xDE0128
    r.cpu.x[19] = 0x1919
    r.cpu.x[30] = 0x3030
    r.cpu.sp = 0x7000000
    out = r.cpu.run(0x99E800, w)
    assert r.cpu.x[20] == r.ctx and r.cpu.x[21] == 0xDE0128
    assert r.cpu.x[19] == 0x1919 and r.cpu.x[30] == 0x3030
    assert r.cpu.sp == 0x7000000
    assert r.mem.u(r.ctx + 0x10, 4) == r.esp
    if held and dup:
        assert out == S.MISS_EPILOGUE and stored == [dup]
        assert r.calls == [('dup', held)]
    else:
        assert out == S.MISS + 4 and stored == []
        assert r.cpu.x[8] & 0xFFFFFFFF == r.mem.u(r.ctx + 0x14, 4)  # replayed


PB, STRIDE = 0x6579E77, 0x7800


def test_the_stock_footstep_row_is_always_planned():
    b = Box(se(16))
    b.plan()
    assert (159, 0) in b.loads()


def test_footstep_variants_from_the_cmfs_trailer_load_first_and_keep():
    # fallback = variants 0..1, triangles use 4..6 and 0..1 again; 0xFF is
    # silent, count 0 is the stock row
    cmfs = ((0, 2, 0), [(4, 3, 0), (0, 2, 1), (0xFF, 0, 0), (9, 0, 0)])
    b = Box(se(16), foot=(PB, STRIDE), cmfs=cmfs)
    b.plan()
    foot = [(i, o) for i, o in b.loads() if i == 744]
    assert foot == [(744, PB + k * STRIDE) for k in (4, 5, 6, 0, 1)]
    assert b.loads()[:5] == foot                     # footsteps first
    # each load: sp_prep sets row 744 like fieldsteps, then sfx_load, take
    for i, (sid, off) in enumerate(b.loads()):
        b.call('sp_prep', ST, PT, TAB, i)
        if sid == 744:
            assert struct.unpack('<II', b.gr(0xDE0E84 + 743 * 0x1C, 8)) == (STRIDE, off)
        b.load(sid, 0x7000 + i)
    assert b.st(S.ST_NENT)[0] == len(b.loads())
    # a step on variant 5 finds its copy
    b.g(0xDE0E84 + 743 * 0x1C + 4, struct.pack('<I', PB + 5 * STRIDE))
    b.g(EBP - 4, struct.pack('<I', 743))
    b.uc.mem_write(CTX, struct.pack('<8I', 0, 0, 0, 0, 0, EBP, 0, 0))
    assert b.call('sp_find', ST, PT, CTX) == 0x7001
    # next field with variants 5 and 7: keeps 5, releases the rest of 744
    b.set_field(se(16), 0x42, cmfs=((5, 1, 0), [(7, 1, 0)]))
    b.plan()
    rel, _ = b.lists()
    assert sorted(rel) == [0x7000, 0x7002, 0x7003, 0x7004]
    assert [(i, o) for i, o in b.loads() if i == 744] == [(744, PB + 7 * STRIDE)]


def test_no_route_no_trailer_no_footstep_variants():
    b = Box(se(16), foot=(PB, STRIDE))               # field without CMFS
    b.plan()
    assert [i for i, _ in b.loads() if i == 744] == []
    b = Box(se(16), foot=None, cmfs=((0, 2, 0), []))  # bridge not installed
    b.plan()
    assert [i for i, _ in b.loads() if i == 744] == []
