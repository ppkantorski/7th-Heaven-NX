#!/usr/bin/env python3
"""
tests/test_campos.py -- BUILD 610, the camera's unfloored follow position,
executed in arm64emu at the real dead-space addresses with the guest faked.

The stock conversion (0x661098) and the projection (0x64314F) are stubbed as
native calls; the translator is the paged model, so a cave that indexed off
one translated pointer would read the wrong page and fail here.

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
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import arm64emu                                                # noqa: E402
import ff7nx_campos as C                                       # noqa: E402

M64 = (1 << 64) - 1
CTX = 0x10000000                  # host: the guest register block (x21)
ESP = 0x001F0000                  # guest
IN = 0x001F8FFC                   # guest short[3], straddling a page edge
OUT = 0x001F9FF8                  # guest float[3], straddling a page edge
BASE = 0x994B50 + 0x3600          # the 'campos' part on the stock module
RET = 0x00A963F8                  # after the hooked bl in 0x66307D
TAG = (C.MAGIC << 32)


def f32(v):
    return struct.unpack('<f', struct.pack('<I', v & 0xFFFFFFFF))[0]


class Harness:
    def __init__(self, pos, view_z, idx=0, shorts=None, offs=None, hand=0):
        self.mem = arm64emu.Mem()
        self.cpu = arm64emu.Cpu(self.mem, paged=True, native={
            C.CONV: self._conv, C.PROJ: self._proj})
        self.proj_x24 = None
        cpu = self.cpu
        cpu.sp = 0x7FFF0000
        cpu.x[30] = RET
        cpu.x[21] = CTX
        self.w32h(CTX + 0x10, ESP)
        self.w32(ESP, IN)
        self.w32(ESP + 4, OUT)
        base = C.EV_POS + C.EV_STRIDE * idx
        for k, v in enumerate(pos):
            self.w32(base + 4 * k, v & 0xFFFFFFFF)
        self.w16(C.VIEW_Z, view_z)
        if offs is not None:
            for off, v in zip((C.OFF_X, C.OFF_Y, C.OFF_Z), offs):
                self.w32(base + off, v & 0xFFFFFFFF)
            self.w16(C.HAND_Z, hand)
        if shorts is None:
            shorts = (self.floored(pos, view_z) if offs is None
                      else self.hand(pos, offs, hand))
        for k, v in enumerate(shorts):
            self.w16(IN + 2 * k, v)

    @staticmethod
    def floored(pos, view_z):
        def s16(v):
            v &= 0xFFFF
            return v - 0x10000 if v & 0x8000 else v
        x, y, z = (p >> 12 for p in pos)
        return s16(x), s16(y), s16(s16(z) + view_z)

    @staticmethod
    def hand(pos, offs, hand):
        """x86 0x644563..0x644601: floor + offset, z + (short)(h<<7>>9)."""
        def s16(v):
            v &= 0xFFFF
            return v - 0x10000 if v & 0x8000 else v
        h = s16(hand)
        hz = s16((h << 7) >> 9)
        x, y, z = (s16(p >> 12) for p in pos)
        return s16(x + offs[0]), s16(y + offs[1]), s16(z + offs[2] + hz)

    def g(self, va):
        return self.cpu.guest_to_host(va)

    def w32h(self, a, v):
        self.mem.setu(a, v & 0xFFFFFFFF, 4)

    def w32(self, va, v):
        for i in range(4):
            self.mem.setu(self.g(va + i), (v >> (8 * i)) & 0xFF, 1)

    def w16(self, va, v):
        for i in range(2):
            self.mem.setu(self.g(va + i), (v >> (8 * i)) & 0xFF, 1)

    def r32(self, va):
        return sum(self.mem.u(self.g(va + i), 1) << (8 * i) for i in range(4))

    def r16s(self, va):
        v = self.mem.u(self.g(va), 1) | (self.mem.u(self.g(va + 1), 1) << 8)
        return v - 0x10000 if v & 0x8000 else v

    def _conv(self, cpu):
        # the stock body: out[k] = (float) in[k]; clobbers volatile regs
        for k in range(3):
            v = struct.unpack('<I', struct.pack('<f', float(self.r16s(IN + 2 * k))))[0]
            self.w32(OUT + 4 * k, v)
        for r in list(range(0, 19)):
            cpu.x[r] = 0xBADBADBAD
        for r in range(8):
            cpu.fp[r] = 0x7FC00000

    def _proj(self, cpu):
        self.proj_x24 = cpu.x[24]
        for r in list(range(0, 19)):
            cpu.x[r] = 0xBADBADBAD

    def out(self):
        return [f32(self.r32(OUT + 4 * k)) for k in range(3)]


def _conv_code():
    words = C.conv_body(lambda i: BASE + 4 * i)
    return {BASE + 4 * i: w for i, w in enumerate(words)}


def _tag_code(guest, width, kind=0):
    at = BASE + 0x200
    words = C.tag_body(lambda i: at + 4 * i, guest, width, kind)
    return at, {at + 4 * i: w for i, w in enumerate(words)}


SAVED = (19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29)


class Conv(unittest.TestCase):
    def run_conv(self, h, x24):
        cpu = h.cpu
        cpu.x[24] = x24
        for r in SAVED:
            if r not in (21, 24):
                cpu.x[r] = 0x1111000000 + r
        before = {r: cpu.x[r] for r in SAVED}
        sp = cpu.sp
        out = cpu.run(BASE, _conv_code(), max_steps=10000)
        self.assertEqual(out, RET)
        self.assertEqual(cpu.sp, sp)
        for r in SAVED:
            self.assertEqual(cpu.x[r], before[r], 'x%d not preserved' % r)
        return h.out()

    def test_precise_when_tagged(self):
        pos = (0x12345 * 7, -0x4567 * 9 - 123, 0x9ABC * 3 + 77)
        h = Harness(pos, view_z=-5, idx=3)
        got = self.run_conv(h, TAG | 3)
        want = [pos[0] / 4096.0, pos[1] / 4096.0, pos[2] / 4096.0 - 5]
        for g, w in zip(got, want):
            self.assertAlmostEqual(g, w, places=3)

    def test_stock_untagged(self):
        pos = (0x12345 * 7, -0x4567 * 9 - 123, 0x9ABC * 3 + 77)
        h = Harness(pos, view_z=-5, idx=3)
        got = self.run_conv(h, 0x0000000012345678)
        self.assertEqual(got, [float(v) for v in h.floored(pos, -5)])

    def test_stock_when_the_shorts_are_not_that_model(self):
        pos = (0x12345 * 7, -0x4567 * 9 - 123, 0x9ABC * 3 + 77)
        fl = Harness.floored(pos, -5)
        for k in range(3):
            shorts = list(fl)
            shorts[k] += 1
            h = Harness(pos, view_z=-5, idx=3, shorts=shorts)
            got = self.run_conv(h, TAG | 3)
            self.assertEqual(got, [float(v) for v in shorts])

    def test_cursor_kind(self):
        pos = (0x12345 * 7, -0x4567 * 9 - 123, 0x9ABC * 3 + 77)
        offs = (3, -4, 12)
        for hand in (0, 200, -300, 0x7FFF):
            h = Harness(pos, view_z=-5, idx=4, offs=offs, hand=hand)
            got = self.run_conv(h, TAG | (1 << 16) | 4)
            hs = ((hand + 0x8000) & 0xFFFF) - 0x8000
            hz = ((hs << 7) >> 9)
            hz = ((hz + 0x8000) & 0xFFFF) - 0x8000
            want = [pos[0] / 4096.0 + 3, pos[1] / 4096.0 - 4,
                    pos[2] / 4096.0 + 12 + hz]
            for g, w in zip(got, want):
                self.assertAlmostEqual(g, w, places=2)

    def test_cursor_tag_on_camera_shorts_is_stock(self):
        # a cursor tag whose shorts lack the offsets: stock floats
        pos = (4096 * 10 + 5, 4096 * 20 + 6, 4096 * 30 + 7)
        h = Harness(pos, view_z=0, idx=2, offs=(1, 1, 1), hand=0,
                    shorts=(10, 20, 30))
        got = self.run_conv(h, TAG | (1 << 16) | 2)
        self.assertEqual(got, [10.0, 20.0, 30.0])

    def test_unknown_kind_is_stock(self):
        pos = (4096 * 10 + 5, 4096 * 20 + 6, 4096 * 30 + 7)
        h = Harness(pos, view_z=0, idx=2)
        got = self.run_conv(h, TAG | (2 << 16) | 2)
        self.assertEqual(got, [10.0, 20.0, 30.0])

    def test_stock_for_a_bad_index(self):
        pos = (4096 * 10, 4096 * 20, 4096 * 30)
        h = Harness(pos, view_z=0, idx=0)
        got = self.run_conv(h, TAG | 0xFFFF)
        self.assertEqual(got, [10.0, 20.0, 30.0])

    def test_monotonic_along_a_slope(self):
        """Walking a diagonal slope in small steps: the stock inputs step
        per component; the tagged ones are the line itself."""
        prev = None
        for t in range(0, 4096 * 6, 173):
            pos = (100 * 4096 + t, -50 * 4096 - (t * 3) // 2, 7 * 4096 + (t * 5) // 3)
            h = Harness(pos, view_z=2, idx=1)
            got = self.run_conv(h, TAG | 1)
            self.assertAlmostEqual(got[0], pos[0] / 4096.0, places=3)
            self.assertAlmostEqual(got[2], pos[2] / 4096.0 + 2, places=3)
            if prev is not None:
                self.assertGreater(got[0], prev[0])
                self.assertLess(got[1], prev[1])
                self.assertGreater(got[2], prev[2])
            prev = got


class Tag(unittest.TestCase):
    def run_tag(self, guest, width, raw, kind=0):
        h = Harness((0, 0, 0), 0)
        if width == 2:
            h.w16(guest, raw)
        else:
            h.mem.setu(h.g(guest), raw & 0xFF, 1)
        cpu = h.cpu
        cpu.x[24] = 0x5555666677778888
        cpu.x[30] = 0x9F9E18
        sp = cpu.sp
        at, code = _tag_code(guest, width, kind)
        out = cpu.run(at, code, max_steps=1000)
        self.assertEqual(out, 0x9F9E18)
        self.assertEqual(cpu.x[24], 0x5555666677778888)
        self.assertEqual(cpu.sp, sp)
        return h.proj_x24

    def test_player_index(self):
        self.assertEqual(self.run_tag(0xCC162C, 2, 5), TAG | 5)

    def test_negative_index_is_out_of_range(self):
        self.assertEqual(self.run_tag(0xCC162C, 2, 0xFFFF), TAG | 0xFFFF)
        self.assertEqual(self.run_tag(0xCC0DA6, 1, 0xFF), TAG | 0xFFFF)

    def test_byte_index(self):
        self.assertEqual(self.run_tag(0xCC0DA6, 1, 9), TAG | 9)

    def test_cursor_kind(self):
        self.assertEqual(self.run_tag(0xCC162C, 2, 7, kind=1),
                         TAG | (1 << 16) | 7)


class Module(unittest.TestCase):
    DUMP = os.path.expanduser('~/mnt/7th_heaven_nx/dump/exefs/main')

    def test_sites_on_the_stock_module(self):
        if not os.path.exists(self.DUMP):
            return
        import ff7nx_audio_cave as AC
        blob = open(self.DUMP, 'rb').read()
        _segs, raw = AC.segments(blob)
        self.assertEqual(C.read_state(raw[0]), 'stock')

    def test_apply_round_trip(self):
        if not os.path.exists(self.DUMP):
            return
        import tempfile
        import ff7nx_audio_cave as AC
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, 'main')
            rep = C.apply_to_nso(self.DUMP, dest, self.DUMP)
            _segs, raw = AC.segments(open(dest, 'rb').read())
            self.assertEqual(C.read_state(raw[0]), 'on')
            self.assertEqual(rep['entries']['conv'], BASE)
            text = raw[0]
            words = C.conv_body(lambda i: BASE + 4 * i)
            got = list(struct.unpack_from('<%dI' % len(words), text, BASE))
            self.assertEqual(got, words)
            with self.assertRaises(ValueError):
                C.apply_to_nso(dest, dest + '2', self.DUMP)


if __name__ == '__main__':
    unittest.main()


# ---------------------------------------------------------------- walk height
WALK_RET = 0x9DED60
EBP_W = 0x002F0000


def _walk_code():
    at = BASE + 0x300
    words = C.walk_body(lambda i: at + 4 * i)
    return at, {at + 4 * i: w for i, w in enumerate(words)}


def plane_z(v, X, Y):
    """0x649F8A with the real point: exact rational, truncated to 20.12."""
    a = [v[1][i] - v[0][i] for i in range(3)]
    b = [v[2][i] - v[1][i] for i in range(3)]
    nx = a[2] * b[1] - a[1] * b[2]
    ny = a[0] * b[2] - a[2] * b[0]
    nz = a[1] * b[0] - a[0] * b[1]
    d = nx * v[0][0] + ny * v[0][1] + nz * v[0][2]
    num = d * 4096 - nx * X - ny * Y
    q = abs(num) // abs(nz)
    return q if (num >= 0) == (nz > 0) else -q


def stock_z(v, X, Y):
    def t(u):
        return int(u / 4096) if u < 0 else u >> 12
    a = [v[1][i] - v[0][i] for i in range(3)]
    b = [v[2][i] - v[1][i] for i in range(3)]
    nx = a[2] * b[1] - a[1] * b[2]
    ny = a[0] * b[2] - a[2] * b[0]
    nz = a[1] * b[0] - a[0] * b[1]
    num = nx * v[0][0] + ny * v[0][1] + nz * v[0][2] - nx * t(X) - ny * t(Y)
    q = abs(num) // abs(nz)
    return q if (num >= 0) == (nz > 0) else -q


class WalkHarness(Harness):
    def __init__(self, tri_verts, X, Y, idx=3, tri=7, stock=None):
        super().__init__((0, 0, 0), 0)
        self.cpu.x[22] = CTX
        self.cpu.x[30] = WALK_RET
        self.w32h(CTX + 0x14, EBP_W)
        self.w16(EBP_W + 8, idx)
        self.w32(EBP_W - 0x50, X)
        self.w32(EBP_W - 0x4C, Y)
        self.w16(C.TRI_OFF + C.EV_STRIDE * idx, tri)
        arr = 0x00500000
        self.w32(C.WALK_TRIS, arr)
        for k, vtx in enumerate(tri_verts):
            for j, c in enumerate(vtx):
                self.w16(arr + 24 * tri + 8 * k + 2 * j, c)
        self.cpu.x[8] = stock_z(tri_verts, X, Y) if stock is None else stock


# wcrimb_2 triangle 7 (the upper cable, dz/dy ~ 10), from the built archive
TRI7 = [(-22, 1442, 3619), (60, 1420, 3452), (-3, 1423, 3440)]


class Walk(unittest.TestCase):
    def run_walk(self, h):
        cpu = h.cpu
        for r in SAVED:
            if r not in (21, 22, 24):
                cpu.x[r] = 0x2222000000 + r
        before = {r: cpu.x[r] for r in SAVED}
        sp = cpu.sp
        at, code = _walk_code()
        out = cpu.run(at, code, max_steps=10000)
        self.assertEqual(out, WALK_RET)
        self.assertEqual(cpu.sp, sp)
        for r in SAVED:
            self.assertEqual(cpu.x[r], before[r], 'x%d not preserved' % r)
        v = cpu.x[8] & 0xFFFFFFFF
        return v - (1 << 32) if v & 0x80000000 else v

    def test_real_height_on_the_cable(self):
        for X, Y in ((10 * 4096 + 1234, 1430 * 4096 + 777),
                     (20 * 4096 + 5, 1425 * 4096 + 4000),
                     (0 * 4096 + 2048, 1428 * 4096 + 1)):
            h = WalkHarness(TRI7, X, Y)
            self.assertEqual(self.run_walk(h), plane_z(TRI7, X, Y))

    def test_smooth_where_stock_hops(self):
        """Walking up the cable in small steps: stock z jumps ~10 units at
        once, the patched height moves by at most a step's worth."""
        prev = None
        hops = 0
        X = 10 * 4096
        for k in range(200):
            Y = 1428 * 4096 + k * 97
            h = WalkHarness(TRI7, X, Y)
            z = self.run_walk(h)
            self.assertEqual(z, plane_z(TRI7, X, Y))
            if prev is not None:
                self.assertLessEqual(abs(z - prev), 11 * 97 + 4096 // 64)
                hops = max(hops, abs(stock_z(TRI7, X, Y) * 4096
                                     - stock_z(TRI7, X, Y - 97) * 4096))
            prev = z
        self.assertGreaterEqual(hops, 9 * 4096)   # stock really hopped

    def test_stock_on_bad_index(self):
        h = WalkHarness(TRI7, 4096 * 10, 4096 * 1430, idx=0xFFF0)
        self.assertEqual(self.run_walk(h), stock_z(TRI7, 4096 * 10, 4096 * 1430) << 12)

    def test_stock_on_degenerate_triangle(self):
        flat = [(0, 0, 5), (10, 0, 5), (20, 0, 5)]      # nz == 0
        h = WalkHarness(flat, 4096 * 3, 0, stock=5)
        self.assertEqual(self.run_walk(h), 5 << 12)

    def test_stock_when_far_off(self):
        h = WalkHarness(TRI7, 4096 * 10 + 99, 4096 * 1430, stock=1000)
        self.assertEqual(self.run_walk(h), 1000 << 12)


class Sites(unittest.TestCase):
    DUMP = os.path.expanduser('~/mnt/7th_heaven_nx/dump/exefs/main')

    def test_sites_proven_on_stock(self):
        if not os.path.exists(self.DUMP):
            return
        import ff7nx_audio_cave as AC
        _s, raw = AC.segments(open(self.DUMP, 'rb').read())
        self.assertEqual(C.sites_proven(raw[0]), [])
        self.assertEqual(C.walk_state(raw[0]), 'stock')
        self.assertTrue(C._walk_context_ok(raw[0]))

    def test_camera_site_is_the_camera(self):
        """The camera site is the one whose input vector is ebp-0x18."""
        sites = {s[1]: s[0] for s in C.FOLLOW_SITES}
        self.assertEqual(C.SITE_PROOF[sites[0x6442FD]][1], 0x18)
        self.assertEqual(C.SITE_PROOF[sites[0x64460D]][1], 0x28)


# ---------------------------------------------------------------- wall facing
FACE_EBP = 0x003F0000
MODEL_A6 = 0xCC16A6


class Face(unittest.TestCase):
    def setUp(self):
        self.h = Harness((0, 0, 0), 0)
        self.h.cpu.x[25] = CTX
        self.h.w32h(CTX + 0x14, FACE_EBP)

    def _run(self, body, ret, x0_guest):
        cpu = self.h.cpu
        at = BASE + 0x500
        words = body(lambda i: at + 4 * i)
        code = {at + 4 * i: w for i, w in enumerate(words)}
        cpu.x[30] = ret
        cpu.x[0] = cpu.guest_to_host(x0_guest)
        for r in SAVED:
            if r not in (21, 24, 25, 27, 28):
                cpu.x[r] = 0x3333000000 + r
        before = {r: cpu.x[r] for r in SAVED if r != 27}
        sp = cpu.sp
        out = cpu.run(at, code, max_steps=1000)
        self.assertEqual(out, ret)
        self.assertEqual(cpu.sp, sp)
        for r, v in before.items():
            self.assertEqual(cpu.x[r], v, 'x%d' % r)

    def test_store_parks_the_pushed_direction(self):
        cpu = self.h.cpu
        cpu.x[28] = 0x5A
        self._run(C.face_store_body, 0x9D81B0, MODEL_A6)
        self.assertEqual(self.h.mem.u(self.h.g(MODEL_A6), 1), 0x5A)
        self.assertEqual(self.h.mem.u(self.h.g(FACE_EBP - 0x30), 1), 0x5A)

    def test_load_returns_pushed_and_undoes_the_rotation(self):
        cpu = self.h.cpu
        self.h.mem.setu(self.h.g(FACE_EBP - 0x30), 0x5A, 1)
        self.h.mem.setu(self.h.g(MODEL_A6), 0x5A - 16, 1)   # rotated by walk
        cpu.x[8] = 0x1234 * 0x88                              # live after
        self._run(C.face_load_body, 0x9D82A0, MODEL_A6)
        self.assertEqual(cpu.x[27] & 0xFF, 0x5A)
        self.assertEqual(self.h.mem.u(self.h.g(MODEL_A6), 1), 0x5A)
        self.assertEqual(cpu.x[8], 0x1234 * 0x88)

    def test_sites_stock(self):
        dump = os.path.expanduser('~/mnt/7th_heaven_nx/dump/exefs/main')
        if not os.path.exists(dump):
            return
        import ff7nx_audio_cave as AC
        _s, raw = AC.segments(open(dump, 'rb').read())
        self.assertEqual(C.face_state(raw[0]), 'stock')
        self.assertTrue(C._face_context_ok(raw[0]))
