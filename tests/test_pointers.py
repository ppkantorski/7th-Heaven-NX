#!/usr/bin/env python3
"""tests/test_pointers.py -- BUILD 611: the pointer hand and the exit arrows,
executed in arm64emu at real dead-space addresses with the guest faked.

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
import ff7nx_pointers as P                                     # noqa: E402

CTX = 0x10000000
FP = 0x11000000                  # x87 state block (host)
BASE = 0x994B50 + 0x3C00
BSS = 0x3FF0000                  # host address the caves adrp to
EBP = 0x001F0000
PROJ = os.path.expanduser('~/mnt/7th_heaven_nx')
DUMP = os.path.join(PROJ, 'dump', 'exefs', 'main')
BUILT_FLEVEL = os.path.join(PROJ, 'sdout', 'atmosphere', 'contents',
                            '0100A5B00BDC6000', 'romfs', 'ff7', 'workingdir',
                            'data', 'field', 'flevel.lgp')
TAG = P.MAGIC << 32


def f2u(v):
    return struct.unpack('<I', struct.pack('<f', v))[0]


def u2f(v):
    return struct.unpack('<f', struct.pack('<I', v & 0xFFFFFFFF))[0]


class H:
    def __init__(self, native=None):
        self.mem = arm64emu.Mem()
        self.cpu = arm64emu.Cpu(self.mem, paged=True, native=native or {})
        self.cpu.sp = 0x7FFF0000

    def g(self, va):
        return self.cpu.guest_to_host(va)

    def w(self, va, v, n=4):
        for i in range(n):
            self.mem.setu(self.g(va + i), (v >> (8 * i)) & 0xFF, 1)

    def r(self, va, n=4):
        return sum(self.mem.u(self.g(va + i), 1) << (8 * i) for i in range(n))

    def run(self, words, at, ret, steps=20000):
        code = {at + 4 * i: x for i, x in enumerate(words)}
        self.cpu.x[30] = ret
        return self.cpu.run(at, code, max_steps=steps)


class Ftol(unittest.TestCase):
    def _go(self, x24, value):
        seen = []
        h = H({P.FTOL: lambda cpu: seen.append(1)})
        h.cpu.x[24] = x24
        h.cpu.x[22] = FP
        h.mem.setu(FP + P.FP_TOP, 5, 4)
        h.mem.setu(FP + 5 * 8, struct.unpack('<Q', struct.pack('<d', value))[0], 8)
        h.mem.setu(BSS + 4, 0x12345678, 4)
        sp = h.cpu.sp
        words = P.ftol_body(lambda i: BASE + 4 * i, BSS, 4)
        out = h.run(words, BASE, 0xA969C8)
        self.assertEqual(out, 0xA969C8)
        self.assertEqual(h.cpu.sp, sp)
        self.assertEqual(seen, [1])
        return h.mem.u(BSS + 4, 4)

    def test_hand_call_is_captured(self):
        self.assertAlmostEqual(u2f(self._go(TAG | (1 << 16) | 3, -12.625)),
                               -12.625, places=4)

    def test_camera_call_is_not(self):
        self.assertEqual(self._go(TAG | 3, 5.5), 0x12345678)

    def test_untagged_is_not(self):
        self.assertEqual(self._go(0x1234, 5.5), 0x12345678)


class Submit(unittest.TestCase):
    def test_remainder_added_to_vertices(self):
        seen = []
        h = H({P.SUBMIT_TGT: None})
        h.cpu.native = {}
        h.cpu.x[23] = CTX
        h.mem.setu(CTX + 0x14, EBP, 4)
        h.mem.setu(BSS, f2u(37.75), 4)
        h.mem.setu(BSS + 4, f2u(-20.25), 4)
        h.w(P.VIEW_MULT, 2)
        verts = [(100.0, 50.0), (116.0, 50.0), (100.0, 66.0), (116.0, 66.0)]
        for k, (x, y) in enumerate(verts):
            h.w(EBP - 0x48 + 8 * k, f2u(x))
            h.w(EBP - 0x44 + 8 * k, f2u(y))
        h.cpu.x[19], h.cpu.x[20] = 0x1919, 0x2020
        sp = h.cpu.sp
        words = P.submit_body(lambda i: BASE + 4 * i, BSS)
        out = h.run(words, BASE, 0x928BE8)
        self.assertEqual(out, P.SUBMIT_TGT)        # tail call
        self.assertEqual(h.cpu.x[30], 0x928BE8)    # returns past the site
        self.assertEqual(h.cpu.sp, sp)
        self.assertEqual((h.cpu.x[19], h.cpu.x[20]), (0x1919, 0x2020))
        for k, (x, y) in enumerate(verts):
            self.assertAlmostEqual(u2f(h.r(EBP - 0x48 + 8 * k)), x + 1.5, places=4)
            self.assertAlmostEqual(u2f(h.r(EBP - 0x44 + 8 * k)), y - 0.5, places=4)

    def test_nothing_captured_is_no_shift(self):
        h = H()
        h.cpu.x[23] = CTX
        h.mem.setu(CTX + 0x14, EBP, 4)
        h.w(P.VIEW_MULT, 2)
        h.w(EBP - 0x48, f2u(100.0))
        words = P.submit_body(lambda i: BASE + 4 * i, BSS)
        h.run(words, BASE, 0x928BE8)
        self.assertEqual(u2f(h.r(EBP - 0x48)), 100.0)


class Clamp(unittest.TestCase):
    def _go(self, x, vp=0, m=2):
        h = H()
        h.cpu.x[19] = P.HAND_Y
        h.cpu.x[24], h.cpu.x[25] = 0x2424, 0x2525
        h.w(P.VIEW_MULT, m)
        h.w(P.VIEW_X, vp & 0xFFFFFFFF)
        h.w(P.HAND_X, x & 0xFFFF, 2)
        sp = h.cpu.sp
        out = h.run(P.clamp_body(lambda i: BASE + 4 * i), BASE, 0)
        self.assertEqual(out, P.CLAMP_RESUME)
        self.assertEqual(h.cpu.sp, sp)
        self.assertEqual((h.cpu.x[24], h.cpu.x[25]), (0x2424, 0x2525))
        self.assertEqual(h.cpu.x[19] & 0xFFFFFFFF, P.HAND_Y)
        self.assertEqual(h.cpu.x[20] & 0xFFFFFFFF, P.VIEW_X)
        self.assertEqual(h.cpu.x[21] & 0xFFFFFFFF, P.VIEW_MULT)
        self.assertEqual(h.cpu.x[22] & 0xFFFFFFFF, P.HAND_X)
        v = h.r(P.HAND_X, 2)
        return v - 0x10000 if v & 0x8000 else v

    def test_inside_the_wide_frame_is_kept(self):
        self.assertEqual(self._go(700), 700)       # stock clamps to 640
        self.assertEqual(self._go(-80), -80)       # stock clamps to 0

    def test_outside_is_clamped_to_16_9(self):
        self.assertEqual(self._go(900), 746)       # 373 * 2
        self.assertEqual(self._go(-200), -106)     # -53 * 2


class Flag(unittest.TestCase):
    def _go(self, flag, fid, i, masks):
        h = H()
        table = BASE + 0x800
        for k, m in enumerate(masks):
            h.mem.setu(table + 2 * k, m, 2)
        h.cpu.x[27] = CTX
        h.mem.setu(CTX, i, 4)
        h.w(P.FIELD_ID, fid, 2)
        flag_va = 0x00700000
        h.w(flag_va, flag, 1)
        h.cpu.x[0] = h.g(flag_va)
        h.cpu.x[19] = 0x1919
        words = P.flag_body(lambda k: BASE + 4 * k, table, len(masks))
        out = h.run(words, BASE, 0x9FC158)
        self.assertEqual(out, 0x9FC158)
        self.assertEqual(h.cpu.x[19], 0x1919)
        return h.cpu.x[8] & 0xFF

    def test_hidden_gateway(self):
        self.assertEqual(self._go(1, 2, 7, [0, 0, 1 << 7]), 0)

    def test_other_gateway_kept(self):
        self.assertEqual(self._go(1, 2, 6, [0, 0, 1 << 7]), 1)

    def test_other_field_kept(self):
        self.assertEqual(self._go(1, 1, 7, [0, 0, 1 << 7]), 1)

    def test_out_of_range_id_kept(self):
        self.assertEqual(self._go(1, 9, 7, [0, 0, 1 << 7]), 1)

    def test_zero_flag_stays_zero(self):
        self.assertEqual(self._go(0, 2, 6, [0, 0, 0]), 0)


class Table(unittest.TestCase):
    def test_reported_fields(self):
        if not os.path.exists(BUILT_FLEVEL):
            return
        _t, rep = P.arrow_table(BUILT_FLEVEL)
        self.assertEqual(rep.get('mds6_3'), [0])
        self.assertIn(1, rep.get('mrkt1', []))      # the wall path
        self.assertIn(7, rep.get('mrkt2', []))      # the honey bee path


class Module(unittest.TestCase):
    def test_apply(self):
        if not (os.path.exists(DUMP) and os.path.exists(BUILT_FLEVEL)):
            return
        import tempfile
        import ff7nx_audio_cave as AC
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, 'main')
            rep = P.apply_to_nso(DUMP, dest, DUMP, BUILT_FLEVEL)
            blob = open(dest, 'rb').read()
            _s, raw = AC.segments(blob)
            self.assertNotEqual(struct.unpack_from('<I', raw[0], P.FLAG_SITE)[0],
                                P.FLAG_STOCK)
            old = struct.unpack_from('<I', open(DUMP, 'rb').read(), 0x3C)[0]
            self.assertGreaterEqual(struct.unpack_from('<I', blob, 0x3C)[0] - old, 8)
            with self.assertRaises(ValueError):
                P.apply_to_nso(dest, dest + '2', DUMP, BUILT_FLEVEL)


if __name__ == '__main__':
    unittest.main()


# ------------------------------------------------------------------ the rail
HDR = 0x00600000
VPX, VPY = 5, -7


def rail_stock(mode, L, T, R, B, x, y):
    """x86 0x643628, integer, as the game computes it."""
    def sar(v, n):
        return v >> n
    def idiv(a, b):
        q = abs(a) // abs(b)
        return q if (a >= 0) == (b > 0) else -q
    if mode == 1:
        ax, ay, bx, by = L + 160, T + 120, R - 160, B - 120
    else:
        ax, ay, bx, by = L + 160, B - 120, R - 160, T + 120
    dx, dy = bx - ax, by - ay
    t = -(dy * (ay - y) + dx * (ax - x))
    sq = sar(dx * dx + dy * dy, 8)
    return sar(idiv(t * dx, sq), 8) + ax, sar(idiv(t * dy, sq), 8) + ay


def rail_real(mode, L, T, R, B, px, py):
    if mode == 1:
        ax, ay, bx, by = L + 160, T + 120, R - 160, B - 120
    else:
        ax, ay, bx, by = L + 160, B - 120, R - 160, T + 120
    dx, dy = bx - ax, by - ay
    t = ((px - ax) * dx + (py - ay) * dy) / float(dx * dx + dy * dy)
    rx, ry = ax + t * dx, ay + t * dy
    rnd = lambda v: int(math.floor(abs(v) + 0.5)) * (1 if v >= 0 else -1)
    return rnd(rx), rnd(ry)


import math                                                    # noqa: E402


class Rail(unittest.TestCase):
    RNG = (-400, -208, 400, 208)        # mds5_3's built range

    def _go(self, mode, cx, cy, clip=None):
        """cx, cy: the camera projection before the viewport and rounding."""
        h = H({P.RAIL_TGT: None})
        L, T, R, B = self.RNG
        xi, yi = int(cx) - VPX, int(cy) - VPY      # ftol truncates, - vp
        if clip:
            xi, yi = clip
        h.cpu.x[21] = CTX
        h.mem.setu(CTX + 0x14, EBP, 4)
        h.w(EBP - P.RAIL_POINT, xi & 0xFFFF, 2)
        h.w(EBP - P.RAIL_POINT + 2, yi & 0xFFFF, 2)
        h.w(P.TRIGGERS_HDR, HDR)
        h.w(HDR + 0x14, mode, 1)
        for k, v in enumerate((L, T, R, B)):
            h.w(HDR + 0xC + 2 * k, v & 0xFFFF, 2)
        h.w(P.VIEW_X, VPX & 0xFFFFFFFF)
        h.w(P.VIEW_Y, VPY & 0xFFFFFFFF)
        h.mem.setu(BSS + 8, f2u(cx), 4)
        h.mem.setu(BSS + 12, f2u(cy), 4)

        def stock(cpu):
            if mode in (1, 2):
                rx, ry = rail_stock(mode, L, T, R, B, xi, yi)
                h.w(EBP - P.RAIL_POINT, rx & 0xFFFF, 2)
                h.w(EBP - P.RAIL_POINT + 2, ry & 0xFFFF, 2)
        h.cpu.native = {P.RAIL_TGT: stock}
        for r in range(19, 30):
            if r != 21:
                h.cpu.x[r] = 0x4444000000 + r
        before = {r: h.cpu.x[r] for r in range(19, 30)}
        sp = h.cpu.sp
        out = h.run(P.rail_body(lambda i: BASE + 4 * i, BSS), BASE, 0x9FA5B8)
        self.assertEqual(out, 0x9FA5B8)
        self.assertEqual(h.cpu.sp, sp)
        for r, v in before.items():
            self.assertEqual(h.cpu.x[r], v, 'x%d' % r)
        def s16(v):
            return v - 0x10000 if v & 0x8000 else v
        return (s16(h.r(EBP - P.RAIL_POINT, 2)),
                s16(h.r(EBP - P.RAIL_POINT + 2, 2)), (xi, yi))

    def test_real_point_projected(self):
        L, T, R, B = self.RNG
        for cx, cy in ((10.3, 20.7), (-50.9, 33.2), (100.5, -40.25)):
            x, y, _ = self._go(1, cx, cy)
            self.assertEqual((x, y), rail_real(1, L, T, R, B,
                                               cx - VPX, cy - VPY))

    def test_no_back_and_forth_across_the_rail(self):
        """Walking across the rail's direction: stock steps back and forth,
        the fix moves one way."""
        L, T, R, B = self.RNG
        prev_s = prev_f = None
        back_s = back_f = 0
        for k in range(120):
            cx, cy = 0.37 * k, -0.53 * k + 3.3
            x, y, (xi, yi) = self._go(1, cx, cy)
            sx, sy = rail_stock(1, L, T, R, B, xi, yi)
            if prev_f is not None:
                back_f += (x - prev_f[0]) * (prev_f[0] - pprev_f[0]) < 0 if k > 1 else 0
                back_s += (sx - prev_s[0]) * (prev_s[0] - pprev_s[0]) < 0 if k > 1 else 0
                pprev_f, pprev_s = prev_f, prev_s
            else:
                pprev_f, pprev_s = (x, y), (sx, sy)
            prev_f, prev_s = (x, y), (sx, sy)
        self.assertEqual(back_f, 0)
        self.assertGreater(back_s, 0)

    def test_mode_2(self):
        L, T, R, B = self.RNG
        x, y, _ = self._go(2, 12.6, -9.1)
        self.assertEqual((x, y), rail_real(2, L, T, R, B, 12.6 - VPX, -9.1 - VPY))

    def test_not_a_rail_field_is_stock(self):
        x, y, (xi, yi) = self._go(0, 12.6, -9.1)
        self.assertEqual((x, y), (xi, yi))

    def test_clamped_axis_takes_no_remainder(self):
        L, T, R, B = self.RNG
        x, y, (xi, yi) = self._go(1, 900.7, 20.4, clip=(240, 20 - VPY))
        self.assertEqual((x, y), rail_real(1, L, T, R, B, 240, 20.4 - VPY))
