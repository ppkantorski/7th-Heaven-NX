"""BUILD 852: Bahamut ZERO's star draw (x86 0x4859AA) builds its composite
0xAA66C0 from the live view on every frame, and slides the orbit's yaw from
one tick's angle toward the next on the paused frames.

The cave runs in the emulator on its real words with paged guest memory; the
three game functions it calls are stood in by natives that read their
arguments off the emulated x86 stack, pop their return slot as the
translated callees do, clobber x0..x18 and compute the real thing (3x3
fixed-point products, a yaw rotation).

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
import math
import struct
import a64 as A
import arm64emu as E
import ff7nx_bzsky as BZ

CAVE = 0x99E000
CTR = 0x3FEC3C8
CTX_HOST = 0x60000000
ESP0, EBP = 0x7F00, 0x8000
SLOT = 7
ORBIT_BUF = 0x30000


def yaw_of(n):
    """A stand-in for 0x48B98D's eased yaw: monotonic, 0 at 0, 0x1000 at 180."""
    return (0x1000 * n * n) // (180 * 180)


def rot_y(a):
    c, s = math.cos(a * 2 * math.pi / 4096), math.sin(a * 2 * math.pi / 4096)
    return [[int(round(4096 * c)), 0, int(round(4096 * s))],
            [0, 4096, 0],
            [int(round(-4096 * s)), 0, int(round(4096 * c))]]


def mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) >> 12 for j in range(3)]
            for i in range(3)]


VIEW = [[4000, 300, -500], [-200, 3900, 800], [600, -700, 3800]]
FRAME = [[4096, 0, 0], [0, 0, -4096], [0, 4096, 0]]


class Rig:
    def __init__(self, s, phase, mark=0, freq=4):
        self.mem = E.Mem()
        self.calls = []
        self.allocated = False
        self.cpu = E.Cpu(self.mem, paged=True, native={
            BZ.COMPOSE: self.native(self.compose),
            BZ.ROT_MATRIX: self.native(self.rot),
            BZ.ORBIT: self.native(self.orbit)})
        mem = self.mem
        mem.write(BZ.CTX_GOT, struct.pack('<Q', CTX_HOST))
        mem.write(CTX_HOST + 0x10, struct.pack('<II', ESP0, EBP))
        self.w16(BZ.CUR_EFFECT, SLOT)
        self.w16(BZ.EFFECT_DATA + 32 * SLOT + 2, s)
        mem.write(CTR + SLOT, bytes([phase | mark]))
        self.put(BZ.VIEW, VIEW)
        self.put(BZ.FRAME, FRAME)
        self.put(BZ.SKY, [[1, 2, 3], [4, 5, 6], [7, 8, 9]])   # stale
        self.w32(BZ.ORBIT_PTR, 0)
        self.words = BZ.build_cave(CAVE, lambda i: CAVE + 4 * i, CTR, freq)

    # guest memory through the translator's page table
    def h(self, va):
        return self.cpu.guest_to_host(va)

    def w16(self, va, v):
        self.mem.write(self.h(va), struct.pack('<h', v))

    def r16(self, va):
        return struct.unpack('<h', self.mem.read(self.h(va), 2))[0]

    def w32(self, va, v):
        self.mem.write(self.h(va), struct.pack('<I', v & 0xFFFFFFFF))

    def r32(self, va):
        return struct.unpack('<I', self.mem.read(self.h(va), 4))[0]

    def put(self, va, m):
        for i in range(3):
            for j in range(3):
                self.w16(va + 2 * (3 * i + j), m[i][j])

    def get(self, va):
        return [[self.r16(va + 2 * (3 * i + j)) for j in range(3)]
                for i in range(3)]

    def esp(self):
        return struct.unpack('<I', self.mem.read(CTX_HOST + 0x10, 4))[0]

    def native(self, fn):
        def run(cpu):
            esp = self.esp()
            args = [self.r32(esp + 4 + 4 * i) for i in range(3)]
            fn(*args)
            self.mem.write(CTX_HOST + 0x10, struct.pack('<I', esp + 4))
            for r in range(0, 19):
                cpu.x[r] = E.GARBAGE
        return run

    def compose(self, a, b, out):
        self.calls.append(('compose', a, b, out))
        self.put(out, mul(self.get(a), self.get(b)))

    def rot(self, angles, out, *_):
        self.calls.append(('rot', angles, out))
        self.put(out, rot_y(self.r16(angles + 2)))

    def orbit(self, n, *_):
        n = struct.unpack('<i', struct.pack('<I', n))[0]
        self.calls.append(('orbit', n))
        if not self.allocated:
            self.allocated = True
            self.w32(BZ.ORBIT_PTR, ORBIT_BUF)
        p = self.r32(BZ.ORBIT_PTR)
        self.w16(p, 0)
        self.w16(p + 2, yaw_of(n))
        self.w16(p + 4, 0)
        self.put(p + 8, rot_y(yaw_of(n)))
        self.put(BZ.SKY, mul(self.get(p + 8), self.get(BZ.SKY)))

    def run(self):
        code = {BZ.HOOK: A.b(BZ.HOOK, CAVE)}
        code.update({CAVE + 4 * i: w for i, w in enumerate(self.words)})
        self.cpu.sp = 0x7000000
        for q in range(31):
            self.cpu.x[q] = 0x1000 + q
        before = list(self.cpu.x)
        end = self.cpu.run(BZ.HOOK, code, start_pc=BZ.HOOK)
        assert end == BZ.RESUME
        # the displaced `stp x28, x27, [sp, #-0x60]!` ran on the entry sp
        assert self.cpu.sp == 0x7000000 - 0x60
        assert self.mem.read(self.cpu.sp, 16) == struct.pack(
            '<QQ', before[28], before[27])
        assert self.cpu.x[30] == before[30]
        assert all(self.cpu.x[q] == before[q] for q in range(19, 30))
        assert self.esp() == ESP0
        return self.get(BZ.SKY)


VF = mul(VIEW, FRAME)


def c_of(s, phase):
    """The main loop's counter at its last real tick."""
    return s + 35 - (0 if phase == 1 else 1)


def test_outside_the_orbit_the_live_view():
    for s, phase in ((5, 1), (5, 2), (19, 1), (20, 0), (199, 3), (200, 2),
                     (400, 1)):
        rig = Rig(s, phase)
        got = rig.run()
        a = c_of(s, phase) - 55
        if 0 <= a < 180:
            continue
        assert got == VF, (s, phase)
        assert [c[0] for c in rig.calls] == ['compose']
        assert rig.calls[0][1:] == (BZ.VIEW, BZ.FRAME, BZ.SKY)


def test_real_call_is_the_stock_tick():
    for s in (20, 21, 100, 199):
        rig = Rig(s, 1)
        got = rig.run()
        a = s - 20
        assert [c[0] for c in rig.calls] == ['compose', 'orbit']
        assert rig.calls[1] == ('orbit', a)
        assert got == mul(rot_y(yaw_of(a)), VF)


def test_paused_frames_slide_toward_the_next_tick():
    for s in (21, 60, 120, 200):
        for phase, k in ((2, 1), (3, 2), (0, 3)):
            rig = Rig(s, phase)
            got = rig.run()
            a = s - 21
            y0, y1 = yaw_of(a), yaw_of(a + 1)
            want_yaw = y0 + (((y1 - y0) * k) >> 2)
            assert [c for c in rig.calls if c[0] == 'orbit'] == [
                ('orbit', a), ('orbit', a + 1)]
            assert rig.calls[-2] == ('rot', ORBIT_BUF, ORBIT_BUF + 8)
            assert rig.calls[-1] == ('compose', ORBIT_BUF + 8, BZ.SKY, BZ.SKY)
            assert rig.r16(ORBIT_BUF + 2) == want_yaw
            assert got == mul(rot_y(want_yaw), VF), (s, phase)


def test_full_30_half_way():
    rig = Rig(80, 0, freq=2)
    got = rig.run()
    a = 80 - 21
    y0, y1 = yaw_of(a), yaw_of(a + 1)
    assert got == mul(rot_y(y0 + ((y1 - y0) >> 1)), VF)
    rig = Rig(80, 1, freq=2)
    assert rig.run() == mul(rot_y(yaw_of(60)), VF)


def test_marked_slot_left_alone():
    for mark in (0x80, 0x40, 0x20, 0x10, 0x04):
        rig = Rig(100, 2, mark=mark)
        assert rig.run() == [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
        assert rig.calls == []


def test_constants_match_the_dump():
    import nxmap
    path = _nx_os.path.join(_nx_root, 'dump', 'exefs', 'main')
    if not _nx_os.path.exists(path):
        return
    m = nxmap.Main(path)
    assert m.x86_to_arm[0x4859AA] == BZ.HOOK
    assert m.x86_to_arm[0x661E85] == BZ.COMPOSE
    assert m.x86_to_arm[0x662AD8] == BZ.ROT_MATRIX
    assert m.x86_to_arm[0x48B98D] == BZ.ORBIT
    assert struct.unpack_from('<I', m.text, BZ.HOOK)[0] == BZ.HOOK_ORIG
    exe = _nx_os.path.join(_nx_root, 'ff7_en')
    if _nx_os.path.exists(exe):
        assert BZ.check_exe(open(exe, 'rb').read()) == ''


def test_toggle(monkeypatch):
    monkeypatch.delenv(BZ.ENV, raising=False)
    assert BZ.enabled()
    monkeypatch.setenv(BZ.ENV, '0')
    assert not BZ.enabled()
