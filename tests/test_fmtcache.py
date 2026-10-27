"""BUILD 813: audio.fmt read from memory (ff7nx_fmtcache + native/fmtcache.c).

Runs the real blob and the real cave in the emulator; the stock stream read
is a modelled native call at the cave's trampoline.

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
import struct

import pytest

import arm64emu as E                                          # noqa: E402
import ff7nx_fmtcache as F                                    # noqa: E402

C_AT = 0x9A0000
STATE = 0x4D00000
STREAM = 0x6000000
BUF = 0x6100000
SP = 0x7000000
RET = 0x123456
TABLE = 0x9B0000
FMT = 'rom://ff7/workingdir/data/sound/audio.fmt'
DAT = 'rom://ff7/workingdir/data/sound/audio.dat'


class Rig:
    def __init__(self, data, path=FMT, dat_range=(0, 0)):
        self.code = open(F.BLOB, 'rb').read()
        self.r_at = (C_AT + len(self.code) + 15) & ~15
        self.cave = F.read_cave(self.r_at, STATE, C_AT, TABLE)
        self.map = {C_AT + i: struct.unpack_from('<I', self.code, i)[0]
                    for i in range(0, len(self.code), 4)}
        for i, w in enumerate(self.cave):
            self.map[self.r_at + 4 * i] = w
        self.tramp = self.r_at + 4 * (len(self.cave) - 2)
        self.mem = E.Mem()
        self.mem.write(C_AT, self.code)    # its read-only data is read, too
        self.mem.write(TABLE, struct.pack('<QQ', *dat_range))
        self.cpu = E.Cpu(self.mem, native={self.tramp: self.stock})
        self.data = bytes(data)
        self.stock_calls = 0
        self.open(path)

    def open(self, path):
        p = path.encode() + b'\0'
        self.mem.write(STREAM + 8, p + b'\xAA' * (256 - len(p)))
        self.mem.setu(STREAM + 280, 0, 8)
        self.mem.setu(STREAM + 288, len(self.data), 8)

    def stock(self, cpu):
        """the port's read: copy from the 'file' at the stream position"""
        self.stock_calls += 1
        s, b, n = cpu.x[0], cpu.x[1], cpu.x[2]
        pos = self.mem.u(s + 280, 8)
        k = max(0, min(n, len(self.data) - pos))
        self.mem.write(b, self.data[pos:pos + k])
        self.mem.setu(s + 280, pos + k, 8)
        cpu.x[0] = k

    def read(self, n, seek=None):
        if seek is not None:
            self.mem.setu(STREAM + 280, seek, 8)
        self.mem.write(BUF, b'\x55' * 64)
        self.cpu.sp = SP
        self.cpu.x[0], self.cpu.x[1], self.cpu.x[2] = STREAM, BUF, n
        self.cpu.x[19], self.cpu.x[23] = 0x1919, 0x2323
        self.cpu.x[30] = RET
        out = self.cpu.run(self.r_at, None, code=self.map, max_steps=2000000)
        if out == F.READ + 4:              # the stock body pops what the
            assert self.cpu.sp == SP - 64  # trampoline's displaced word pushed
            assert self.mem.u(SP - 64, 8) == 0x2323
        else:
            assert self.cpu.sp == SP
        assert self.cpu.x[19] == 0x1919
        return out, self.cpu.x[0], self.mem.read(BUF, min(n, 64))


FMTDATA = bytes((i * 7 + 3) & 0xFF for i in range(750 * 24))


def test_the_first_read_fills_and_every_later_read_is_a_copy():
    r = Rig(FMTDATA)
    for row in (744, 744, 159, 744, 0, 749):
        out, n, got = r.read(24, seek=24 * row)
        assert out == RET
        assert n == 24
        assert got == FMTDATA[24 * row:24 * row + 24]
        assert r.mem.u(STREAM + 280, 8) == 24 * row + 24
    assert r.stock_calls == 1              # the fill, once
    assert r.mem.u(STATE + 8, 4) == 6      # hits


def test_reads_at_the_end_return_what_the_stock_read_would():
    r = Rig(FMTDATA)
    out, n, _ = r.read(24, seek=len(FMTDATA) - 10)
    assert (out, n) == (RET, 10)
    assert r.mem.u(STREAM + 280, 8) == len(FMTDATA)
    out, n, _ = r.read(24)
    assert (out, n) == (RET, 0)


def test_the_fill_keeps_the_streams_position():
    r = Rig(FMTDATA)
    out, n, got = r.read(24, seek=24 * 10)
    assert got == FMTDATA[240:264]
    assert r.mem.u(STREAM + 280, 8) == 264


@pytest.mark.parametrize('path', [
    'rom://ff7/workingdir/data/sound/audio.dat',
    'rom://ff7/workingdir/data/sound/audio.fmtx',
    'rom://ff7/workingdir/field/flevel.lgp',
    ''])
def test_other_files_take_the_stock_read(path):
    r = Rig(FMTDATA, path)
    out, _, _ = r.read(24)
    assert out == F.READ + 4               # the trampoline's resume
    assert r.stock_calls == 0
    assert r.cpu.x[0] == STREAM and r.cpu.x[1] == BUF and r.cpu.x[2] == 24
    assert r.cpu.x[30] == RET


def test_case_and_backslashes_still_match():
    r = Rig(FMTDATA, 'ROM://FF7\\WORKINGDIR\\DATA\\SOUND\\AUDIO.FMT')
    out, n, got = r.read(24, seek=48)
    assert (out, n, got) == (RET, 24, FMTDATA[48:72])


def test_a_file_too_big_or_empty_is_never_cached():
    for size in (0, F.CAP + 1):
        r = Rig(FMTDATA)
        r.mem.setu(STREAM + 288, size, 8)
        out, _, _ = r.read(24)
        assert out == F.READ + 4
        out, _, _ = r.read(24)
        assert out == F.READ + 4           # 'bad' sticks
        assert r.stock_calls == 0


def test_a_short_fill_falls_back_for_good():
    r = Rig(FMTDATA[:100])
    r.mem.setu(STREAM + 288, 200, 8)       # the size says more than it reads
    out, _, _ = r.read(24)
    assert out == F.READ + 4
    assert r.stock_calls == 1
    out, _, _ = r.read(24)
    assert out == F.READ + 4
    assert r.stock_calls == 1


def test_the_cave_fits_the_part_and_the_source_is_the_built_one():
    code = open(F.BLOB, 'rb').read()
    assert len(code) + 16 + 16 + 4 * len(F.read_cave(0x1000, STATE, 0, 0)) <= 0x400
    src = open(F.BLOB.replace('.bin', '.c'), 'rb').read()
    want = open(F.BLOB.replace('.bin', '.src.sha256')).read().split()[0]
    assert hashlib.sha256(src).hexdigest() == want


def test_off_switch():
    assert not F.enabled({F.ENV: '0'})
    assert F.enabled({})


# --- 813b: the audio.dat world-footstep bank ------------------------------

DATA = bytes((i * 13 + 5) & 0xFF for i in range(300000))
LO, LEN = 200000, 68 * 1000            # a 68-variant bank, 1000-byte stride


def test_the_bank_is_filled_on_the_first_dat_read_and_steps_are_copies():
    r = Rig(DATA, DAT, (LO, LEN))
    out, n, got = r.read(64, seek=100)          # an ordinary row, outside
    assert out == F.READ + 4                    # the stock read...
    assert r.stock_calls == 1                   # ...after the fill
    assert r.mem.u(STATE + 12, 4) == 1          # dstate: held
    assert r.mem.u(STREAM + 280, 8) == 100      # position kept by the fill
    for v in (0, 67, 5, 0, 31):
        out, n, got = r.read(1000, seek=LO + 1000 * v)
        assert (out, n) == (RET, 1000)
        assert got == DATA[LO + 1000 * v:LO + 1000 * v + 64]
        assert r.mem.u(STREAM + 280, 8) == LO + 1000 * v + 1000
    assert r.stock_calls == 1
    assert r.mem.u(STATE + 16, 4) == 5          # dhits


def test_a_read_crossing_the_bank_edge_is_the_stock_read():
    r = Rig(DATA, DAT, (LO, LEN))
    out, _, _ = r.read(1000, seek=LO - 10)
    assert out == F.READ + 4
    out, _, _ = r.read(1000, seek=LO + LEN - 10)
    assert out == F.READ + 4
    out, n, _ = r.read(10, seek=LO + LEN - 10)
    assert (out, n) == (RET, 10)


def test_no_range_means_dat_is_untouched():
    r = Rig(DATA, DAT, (0, 0))
    out, _, _ = r.read(1000, seek=LO)
    assert out == F.READ + 4
    assert r.stock_calls == 0


def test_a_range_past_the_end_of_the_file_never_fills():
    r = Rig(DATA, DAT, (len(DATA) - 100, 1000))
    for _ in range(2):
        out, _, _ = r.read(100, seek=len(DATA) - 100)
        assert out == F.READ + 4
    assert r.stock_calls == 0
    assert r.mem.u(STATE + 12, 4) == 2          # failed, for good


def test_fmt_and_dat_are_held_side_by_side():
    r = Rig(FMTDATA)
    r.read(24, seek=0)
    r.data = DATA
    r.mem.write(TABLE, struct.pack('<QQ', LO, LEN))
    r.open(DAT)
    out, n, got = r.read(1000, seek=LO + 2000)
    assert (out, n, got) == (RET, 1000, DATA[LO + 2000:LO + 2064])
    r.data = FMTDATA
    r.open(FMT)
    out, n, got = r.read(24, seek=48)
    assert (out, n, got) == (RET, 24, FMTDATA[48:72])
    assert r.stock_calls == 2                   # one fill each


def test_world_steps_range_from_the_plan():
    class Plan:
        world_footsteps = {'payload_base': 0x64C0000, 'payloads': [b'x' * 0x4400] * 68}
    assert F.world_steps_range(Plan) == (0x64C0000, 68 * 0x4400)
    class NoSteps:
        world_footsteps = None
    assert F.world_steps_range(NoSteps) == (0, 0)
    class Huge:
        world_footsteps = {'payload_base': 1, 'payloads': [b'x' * (F.MAX_RANGE + 1)]}
    assert F.world_steps_range(Huge) == (0, 0)
