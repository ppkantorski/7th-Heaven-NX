"""BUILD 860: autosize dialogue boxes (ff7nx_autosize, native/autosize.c),
FFNx's ff7_field_autosize_text_box. The blob runs in unicorn with guest
memory at a fixed host offset and a two-instruction translator; its results
are compared with a Python transcription of FFNx's native-font rule.

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
import random
import struct

import pytest

import ff7nx_autosize as AS                                    # noqa: E402

unicorn = pytest.importorskip('unicorn')
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM            # noqa: E402
from unicorn.arm64_const import (UC_ARM64_REG_X0, UC_ARM64_REG_X1,  # noqa: E402
                                 UC_ARM64_REG_X2, UC_ARM64_REG_SP,
                                 UC_ARM64_REG_LR, UC_ARM64_REG_CPACR_EL1)

CODE, HB, STK = 0x10000, 0x1000000, 0x40000
G2H = CODE + 0x7000
ESP = 0x19EF00
TABLE = 0x00A00000            # where the fake glyph metric table lives
TEXT = 0x00A10000

# movz x9, #0x100, lsl #16 ; add x0, x9, w0, uxtw ; ret
G2H_CODE = struct.pack('<III', 0xD2A02009, 0x8B204120, 0xD65F03C0)


def metrics(seed=1):
    r = random.Random(seed)
    return bytes(r.randrange(256) for _ in range(256))


class Box:
    def __init__(self, table=None, names=None):
        self.uc = uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        uc.reg_write(UC_ARM64_REG_CPACR_EL1, 0x300000)
        for base, size in ((CODE, 0x10000), (HB, 0x1000000), (STK, 0x10000)):
            uc.mem_map(base, size)
        uc.mem_write(CODE, open(AS.BLOB, 'rb').read())
        uc.mem_write(G2H, G2H_CODE)
        uc.mem_write(CODE + 0x8000, struct.pack('<I', 0xD65F03C0))
        self.table = table if table is not None else metrics()
        self.g(TABLE, self.table)
        self.g(AS.SPACING_PTR, struct.pack('<I', TABLE))
        self.g(AS.NAME_INDEX, struct.pack('<12I', 0, 1, 2, 3, 4, 5, 6, 7, 8,
                                          6, 7, 0))
        self.names = names or [bytes((0x23, 0x4C, 0x4F, 0x55, 0x44, 0xFF)),
                               bytes((0x22, 0x41, 0x52, 0x52, 0x45, 0x54,
                                      0xFF))] + [b'\xff'] * 7
        for i, n in enumerate(self.names):
            self.g(AS.NAMES + AS.NAME_STRIDE * i, n)

    def g(self, va, data):
        self.uc.mem_write(HB + va, bytes(data))

    def gr(self, va, n):
        return bytes(self.uc.mem_read(HB + va, n))

    def window(self, win, x, y, w, h, cur=None, special=0):
        wa = AS.WIN_ARRAY + AS.WIN_STRIDE * win
        cw, ch = cur if cur else (max(w // 4, 8), max(h // 4, 8))
        e = bytearray(0x30)
        struct.pack_into('<hhhhhh', e, 4, x, y, w, h, cw, ch)
        e[0x1B] = special
        self.g(wa, e)

    def geom(self, win):
        wa = AS.WIN_ARRAY + AS.WIN_STRIDE * win
        return struct.unpack('<hhhh', self.gr(wa + 4, 8))

    def text(self, win, data, world=0):
        at = TEXT + 0x1000 * win
        self.g(at, data)
        base = AS.WORLD_STRINGS if world else AS.FIELD_STRINGS
        self.g(base + 4 * win, struct.pack('<I', at))

    def open(self, win, world=0):
        self.g(ESP + 4, struct.pack('<I', win & 0xFFFF))
        for r, v in ((UC_ARM64_REG_X0, ESP), (UC_ARM64_REG_X1, G2H),
                     (UC_ARM64_REG_X2, world)):
            self.uc.reg_write(r, v)
        self.uc.reg_write(UC_ARM64_REG_SP, STK + 0x8000)
        self.uc.reg_write(UC_ARM64_REG_LR, CODE + 0x8000)
        self.uc.emu_start(CODE + AS.entries()['as_open'], CODE + 0x8000,
                          count=2000000)
        return self.uc.reg_read(UC_ARM64_REG_X0) & 0xFFFFFFFF


# ----------------------------------------------- FFNx's rule, in Python
EXP = {0xE0: [0] * 10, 0xE1: [0] * 4, 0xE2: [0x0C, 0], 0xE3: [0x0E, 2],
       0xE4: [0xA9, 2]}


def ffnx(text, table, names):
    """(W, H) as FFNx's auto_resize_text_box (native font, non-JP), or None
    where native/autosize.c keeps the authored box."""
    def adv(c):
        return 26 if fixed else 2 * ((table[c] >> 5) + (table[c] & 0x1F))
    w = h = mw = mh = 0
    fixed = False
    i = 0
    while i < 1024:
        c = text[i]
        n = text[i + 1] if i + 1 < len(text) else 0xFF
        if c == 0xFF:
            break
        if 0xFA <= c <= 0xFD:
            return None
        if c == 0xFE:
            if n < 0xD2:
                return None
            if n == 0xE9:
                fixed = not fixed
            elif n in (0xDE, 0xE1):
                w += 50
            elif n == 0xE2:
                w += 60
                i += 4
            i += 2
            continue
        if c in EXP:
            w += sum(adv(x) for x in EXP[c])
        elif 0xEA <= c <= 0xF5:
            idx = [0, 1, 2, 3, 4, 5, 6, 7, 8, 6, 7, 0][c - 0xEA]
            for b in names[idx][:9]:
                if b == 0xFF:
                    break
                w += adv(b)
        elif c == 0xE7:
            mw, w, h = max(mw, w), 0, h + 32
        elif c in (0xE8, 0xE9):
            mw, mh, w, h = max(mw, w), max(mh, h), 0, 0
        else:
            w += adv(c)
        i += 1
    return (max(mw, w) + 32) // 2, (max(mh, h) + 50) // 2


def expect(x, y, w, h, text, table, names):
    r = ffnx(text, table, names)
    if r is None:
        return (x, y, w, h)
    nw, nh = r
    nh = min(nh, h)
    nw, nh = max(0, min(nw, 320)), max(0, min(nh, 224))
    x = max(0, min(x, 320 - nw))
    y = max(0, min(y, 224 - nh))
    return (x, y, nw, nh)


# ------------------------------------------------------------------ tests
def test_blob_is_current():
    src = os.path.join(os.path.dirname(AS.BLOB), 'autosize.c')
    want = open(os.path.join(os.path.dirname(AS.BLOB),
                             'autosize.src.sha256')).read().strip()
    assert hashlib.sha256(open(src, 'rb').read()).hexdigest() == want


def test_short_line_shrinks_width_and_height():
    b = Box()
    b.window(0, 8, 8, 304, 57)
    t = bytes((0x28, 0x45, 0x4C, 0x4C, 0x4F, 0xFF))       # "Hello"
    b.text(0, t)
    assert b.open(0) == 1
    assert b.geom(0) == expect(8, 8, 304, 57, t, b.table, b.names)
    assert b.geom(0)[3] == 25                              # one line


def test_only_the_first_opening_frame():
    b = Box()
    b.window(1, 8, 8, 304, 57, cur=(76 + 38, 14))
    b.text(1, bytes((0x28, 0xFF)))
    assert b.open(1) == 0
    assert b.geom(1) == (8, 8, 304, 57)


def test_special_windows_keep_their_size():
    b = Box()
    b.window(3, 10, 10, 160, 70, special=1)
    b.text(3, bytes((0x28, 0xFF)))
    assert b.open(3) == 0
    assert b.geom(3) == (10, 10, 160, 70)


@pytest.mark.parametrize('win', [4, 0xFFFF, 7])
def test_window_id_out_of_range(win):
    b = Box()
    assert b.open(win) == 0


def test_never_taller_than_authored_and_long_lines_clamp():
    b = Box(table=bytes([0x0F] * 256))         # 30 half-pixels a glyph
    t = bytes([0x21] * 40 + [0xE7] + [0x21] * 3 + [0xE7, 0x21, 0xE7, 0x21,
                                                   0xFF])
    b.window(2, 100, 200, 200, 41)              # authored: two lines
    b.text(2, t)
    assert b.open(2) == 1
    x, y, w, h = b.geom(2)
    assert w == 320 and x == 0                  # (1200 + 32) / 2 -> 320
    assert h == 41 and y == 224 - 41


def test_pages_take_the_largest():
    b = Box(table=bytes([0x0A] * 256))         # 20 half-pixels a glyph
    t = bytes([0x21] * 5 + [0xE7] + [0x21] * 2 + [0xE8] + [0x21] * 9 +
              [0xFF])
    b.window(0, 8, 8, 300, 73)
    b.text(0, t)
    b.open(0)
    assert b.geom(0) == (8, 8, (9 * 20 + 32) // 2, (32 + 50) // 2)


def test_world_reads_the_world_string_table():
    b = Box()
    t = bytes((0x30, 0x31, 0x32, 0xFF))
    b.window(1, 20, 20, 280, 41)
    b.text(1, t, world=1)
    b.g(AS.FIELD_STRINGS + 4, struct.pack('<I', 0))       # field: none
    assert b.open(1, world=0) == 0
    assert b.open(1, world=1) == 1
    assert b.geom(1) == expect(20, 20, 280, 41, t, b.table, b.names)


@pytest.mark.parametrize('t', [
    bytes((0xFA, 0x10, 0xFF)),                  # a JP font page
    bytes((0x21, 0xFE, 0x10, 0xFF)),            # FE below D2
])
def test_unmeasurable_text_keeps_the_box(t):
    b = Box()
    b.window(0, 8, 8, 304, 57)
    b.text(0, t)
    assert b.open(0) == 0
    assert b.geom(0) == (8, 8, 304, 57)


def test_no_glyph_table_keeps_the_box():
    b = Box()
    b.g(AS.SPACING_PTR, struct.pack('<I', 0))
    b.window(0, 8, 8, 304, 57)
    b.text(0, bytes((0x21, 0xFF)))
    assert b.open(0) == 0


def test_names_expansions_and_codes():
    b = Box()
    t = bytes((0xEA, 0x00, 0xEB, 0xE7, 0xE0, 0x21, 0xE2, 0xE4, 0xFE, 0xD2,
               0x21, 0xFE, 0xDE, 0xFE, 0xE9, 0x21, 0x22, 0xFE, 0xE9, 0x23,
               0xFE, 0xE2, 1, 2, 3, 4, 0x24, 0xFF))
    b.window(0, 8, 8, 304, 73)
    b.text(0, t)
    b.open(0)
    assert b.geom(0) == expect(8, 8, 304, 73, t, b.table, b.names)


@pytest.mark.parametrize('seed', range(60))
def test_random_text_matches_ffnx(seed):
    r = random.Random(seed)
    table = metrics(seed)
    b = Box(table=table)
    pool = list(range(0x00, 0xE0)) * 4 + [0xE0, 0xE1, 0xE2, 0xE3, 0xE4,
                                          0xE7, 0xE7, 0xE8, 0xE9] + \
        list(range(0xEA, 0xF6))
    t = []
    for _ in range(r.randrange(1, 300)):
        c = r.choice(pool + [0xFE])
        if c == 0xFE:
            d = r.choice([0xD2, 0xD5, 0xDE, 0xE1, 0xE2, 0xE9, 0xDD])
            t += [0xFE, d] + ([1, 2, 3, 4] if d == 0xE2 else [])
        else:
            t.append(c)
    t = bytes(t + [0xFF])
    x, y = r.randrange(0, 300), r.randrange(0, 220)
    w, h = r.randrange(40, 320), r.randrange(25, 224)
    b.window(0, x, y, w, h)
    b.text(0, t)
    b.open(0)
    assert b.geom(0) == expect(x, y, w, h, t, table, b.names), t.hex()


def test_stub_runs_the_displaced_word_and_returns():
    import a64 as A
    fn = {'as_open': 0x5000}
    for site, world, _what in AS.SITES:
        w = AS.entry_stub(0x3000, site, world, fn)
        assert w[-2] == AS.ENTRY_WORD
        assert w[-1] == A.b(0x3000 + 4 * (len(w) - 1), site + 4)
        assert A.movz(2, world) in w
        bls = [i for i, x in enumerate(w) if x >> 26 == 0x25]
        assert len(bls) == 1
        assert w[bls[0]] == A.bl(0x3000 + 4 * bls[0], 0x5000)


def test_env_default_off():
    assert not AS.enabled({})
    assert AS.enabled({AS.ENV: '1'})
    assert not AS.enabled({AS.ENV: '0'})


DUMP = os.path.join(nxpaths.ROOT, 'dump', 'exefs', 'main')


@pytest.mark.skipif(not os.path.isfile(DUMP), reason='no exefs/main dump')
def test_apply_on_the_stock_module(tmp_path):
    import nxmap
    out = tmp_path / 'main'
    r = AS.apply_to_nso(DUMP, str(out), stock=DUMP)
    m, s = nxmap.Main(str(out)), nxmap.Main(DUMP)
    import a64 as A
    for site, _world, _what in AS.SITES:
        assert struct.unpack_from('<I', m.text, site)[0] == \
            A.b(site, r['stubs'][site])
    lo, hi = s.extent(AS.OWN_DEAD_X86)
    diff = [i for i in range(len(s.text)) if s.text[i] != m.text[i]]
    assert all(lo + AS.OWN_GUARD <= i < hi - AS.OWN_TAIL
               or any(st <= i < st + 4 for st, _w, _x in AS.SITES)
               for i in diff)
