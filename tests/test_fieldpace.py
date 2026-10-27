#!/usr/bin/env python3
"""
tests/test_fieldpace.py -- BUILD 526's hybrid field limiter.

Each frame waits for the EARLIER of its stock deadline and
`previous release + 1/60 s`. Checked here:

  * the exit cave, under arm64emu, records the limiter's last clock reading
    in its BSS slot and replays the displaced load;
  * the start cave, under arm64emu, lowers the baseline only when
    `release + D` is earlier, never raises it, and does nothing before the
    first release;
  * both hooked words are the ones measured, in the stock module and in a
    finished build;
  * the patched module grows BSS by exactly 8 and changes no data segment;
  * the arithmetic (a model, stated as one): a normal frame is paced EXACTLY
    as the stock limiter paces it, and only a long flip is shortened.

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
import shutil
import struct
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import arm64emu as EM                                          # noqa: E402
import ff7nx_audio_cave as AC                                  # noqa: E402
import ff7nx_fieldpace as P                                    # noqa: E402

PROJECT = os.path.join(os.path.dirname(os.path.dirname(_HERE)),
                       '7th_heaven_nx')
DUMP_MAIN = os.path.join(PROJECT, 'dump', 'exefs', 'main')
BUILT_MAIN = os.path.join(PROJECT, 'sdout', 'atmosphere', 'contents',
                          '0100A5B00BDC6000', 'exefs', 'main')

BSS = 0x3FF1000
HOST = 0x500000                     # host page backing guest 0xCFF800..


def translate(cpu):
    """The real translator (+0x10FC3A0) clobbers x8, x9, x10 and the flags.
    The stub does too, so a cave that holds a value across it fails here."""
    cpu.x[0] = HOST + ((cpu.x[0] & 0xFFFFFFFF) - 0xCFF800)
    cpu.x[8] = cpu.x[9] = cpu.x[10] = 0x5A5A5A5A5A5A5A5A
    cpu.n, cpu.z, cpu.c, cpu.v = 1, 1, 0, 1


def run(words, mem, regs, resume):
    code = {0x400000 + 4 * i: w for i, w in enumerate(words)}
    cpu = EM.Cpu(mem, native={AC.GUEST_TRANSLATE: translate,
                              resume: lambda c: None})
    cpu.x = [0] * 32
    for r, v in regs.items():
        cpu.x[r] = v
    cpu.sp = 0x700000
    try:
        cpu.run(0x400000, code)
    except Exception:                                         # noqa: BLE001
        pass                          # ran off the end into `resume`
    return cpu


class ExitCave(unittest.TestCase):
    def test_it_records_the_release(self):
        mem = EM.Mem()
        mem.setu(HOST + 0xE0, 0x1234567890, 8)
        mem.setu(HOST + 0xD8, 0xAAAA, 8)
        mem.setu(0x600014, 0x77, 4)
        cpu = run(P.build_exit_cave(0x400000, lambda i: 0x400000 + 4 * i,
                                    BSS),
                  mem, {20: 0x600000}, P.EXIT_RESUME)
        self.assertEqual(mem.u(BSS, 8), 0x1234567890)
        self.assertEqual(mem.u(HOST + 0xD8, 8), 0xAAAA,
                         'the exit must not touch the baseline itself')
        self.assertEqual(cpu.x[0] & 0xFFFFFFFF, 0x77)


class StartCave(unittest.TestCase):
    D = 39091

    def start(self, release, baseline):
        mem = EM.Mem()
        mem.setu(BSS, release, 8)
        mem.setu(HOST + 0xD8, baseline, 8)
        mem.setu(0x610010, 0x55, 4)
        cpu = run(P.build_start_cave(0x400000, lambda i: 0x400000 + 4 * i,
                                     BSS, self.D),
                  mem, {24: 0x610000}, P.START_RESUME)
        self.assertEqual(cpu.x[8] & 0xFFFFFFFF, 0x55,
                         'the displaced ldr w8, [x24, #0x10] must replay')
        return mem.u(HOST + 0xD8, 8)

    def test_nothing_before_the_first_release(self):
        self.assertEqual(self.start(0, 5000000), 5000000)

    def test_a_normal_frame_keeps_the_stock_baseline(self):
        # frame start 1 ms after release: release + D (2.04 ms) is LATER
        rel = 10000000
        self.assertEqual(self.start(rel, rel + 19200), rel + 19200)

    def test_a_long_tail_pulls_the_baseline_in(self):
        # frame start 7 ms after release: release + D is EARLIER
        rel = 10000000
        self.assertEqual(self.start(rel, rel + 134400), rel + self.D)

    def test_equal_is_left_alone(self):
        rel = 10000000
        self.assertEqual(self.start(rel, rel + self.D), rel + self.D)


class OnlyTheNormalFrameStart(unittest.TestCase):
    def test_527_is_withdrawn(self):
        """527 also hooked the catch-up frame (+0x94BDF4). Hardware: walking
        worse, fence dips worse. Back to 526: one start hook."""
        self.assertEqual(P.START_HOOKS, (0x946674,))
        self.assertNotIn(P.CATCH_UP_START, P.START_HOOKS)


class LateGate(unittest.TestCase):
    """BUILD 531: the overrun only raises the debt flag when the frame is
    late for SIXTY by more than the margin."""
    def flag(self, debt_counts):
        slack = P.late_slack_counts(66, 2.0)
        words = P.build_late_cave(0x400000, lambda i: 0x400000 + 4 * i, slack)
        code = {0x400000 + 4 * i: w for i, w in enumerate(words)}
        cpu = EM.Cpu(EM.Mem())
        cpu.x = [0] * 32
        cpu.x[21] = 0xDEAD
        cpu.x[22] = struct.unpack('<Q', struct.pack('<d', debt_counts))[0]
        end = cpu.run(0x400000, code)
        self.assertEqual(end, P.LATE_RESUME)
        return cpu.x[21]

    def test_slack(self):
        # 1/60 + 2 ms - T66 = 320000 + 38400 - 280909.09
        self.assertAlmostEqual(P.late_slack_counts(66, 2.0), 77490.91, 1)
        self.assertAlmostEqual(P.late_slack_counts(60, 2.0), 48400.0, 1)

    def test_a_frame_late_only_for_66_is_not_padded(self):
        # tin_3: 16.3 ms elapsed -> 1.67 ms past T66
        self.assertEqual(self.flag(0.00167 * P.CPS), 0)

    def test_a_real_overrun_still_catches_up(self):
        self.assertEqual(self.flag(0.010 * P.CPS), 1)       # a 25 ms frame

    def test_the_3x_booster_still_catches_up(self):
        # 3x clock: a 16 ms frame reads 48 ms -> 33 ms past T
        self.assertEqual(self.flag(0.033 * P.CPS), 1)

    def test_boundary(self):
        slack = P.late_slack_counts(66, 2.0)
        self.assertEqual(self.flag(slack), 1)
        self.assertEqual(self.flag(slack - 1), 0)

    def test_nan_does_not_raise_debt(self):
        self.assertEqual(self.flag(float('nan')), 0)

    def test_env(self):
        old = os.environ.get(P.LATE_ENV)
        try:
            os.environ[P.LATE_ENV] = 'off'
            self.assertIsNone(P.late_ms())
            os.environ[P.LATE_ENV] = '3.5'
            self.assertEqual(P.late_ms(), 3.5)
            del os.environ[P.LATE_ENV]
            self.assertEqual(P.late_ms(), P.LATE_DEFAULT_MS)
        finally:
            if old is None:
                os.environ.pop(P.LATE_ENV, None)
            else:
                os.environ[P.LATE_ENV] = old


class BattleLateGate(unittest.TestCase):
    """BUILD 534: the battle limiter's copy of the gate. Flag in w20,
    debt amount double in x20, battle T = cps/div with no fudge."""
    def flag(self, debt_counts):
        slack = P.battle_late_slack_counts(66, 2.0)
        words = P.build_late_cave(0x400000, lambda i: 0x400000 + 4 * i,
                                  slack, reg=20,
                                  resume=P.BATTLE_LATE_RESUME)
        code = {0x400000 + 4 * i: w for i, w in enumerate(words)}
        cpu = EM.Cpu(EM.Mem())
        cpu.x = [0] * 32
        cpu.x[21], cpu.x[22] = 0x2121, 0x2222
        cpu.x[20] = struct.unpack('<Q', struct.pack('<d', debt_counts))[0]
        self.assertEqual(cpu.run(0x400000, code), P.BATTLE_LATE_RESUME)
        self.assertEqual((cpu.x[21], cpu.x[22]), (0x2121, 0x2222))
        return cpu.x[20]

    def test_slack(self):
        # 1/60 + 2 ms - cps/66 = 320000 + 38400 - 290909.09
        self.assertAlmostEqual(P.battle_late_slack_counts(66, 2.0),
                               67490.91, 1)

    def test_late_only_for_66_is_not_padded(self):
        self.assertEqual(self.flag(0.0015 * P.CPS), 0)   # a 16.7 ms frame

    def test_real_overrun_catches_up(self):
        self.assertEqual(self.flag(0.010 * P.CPS), 1)

    def test_nan(self):
        self.assertEqual(self.flag(float('nan')), 0)


class Slack(unittest.TestCase):
    def test_values(self):
        self.assertEqual(P.slack_counts(66), 39091)       # 2.04 ms
        self.assertEqual(P.slack_counts(60), 10000)       # 0.52 ms


class Sites(unittest.TestCase):
    def _check(self, path):
        _s, raw = AC.segments(open(path, 'rb').read())
        t = raw[0]
        self.assertEqual(struct.unpack_from('<I', t, P.EXIT_HOOK)[0],
                         P.EXIT_ORIG)
        for va in P.START_HOOKS + (P.CATCH_UP_START,):
            self.assertEqual(struct.unpack_from('<I', t, va)[0],
                             P.START_ORIG, hex(va))
        self.assertEqual(struct.unpack_from('<I', t, P.LATE_HOOK)[0],
                         P.LATE_ORIG)
        self.assertEqual(struct.unpack_from('<I', t, P.BATTLE_LATE_HOOK)[0],
                         P.BATTLE_LATE_ORIG)

    @unittest.skipUnless(os.path.exists(DUMP_MAIN), 'needs the dumped main')
    def test_stock(self):
        self._check(DUMP_MAIN)

    @unittest.skipUnless(os.path.exists(DUMP_MAIN), 'needs the dumped main')
    def test_patch(self):
        tmp = tempfile.mkdtemp()
        try:
            out = os.path.join(tmp, 'main')
            r = P.apply_to_nso(DUMP_MAIN, out, 66, 'hybrid')
            before = open(DUMP_MAIN, 'rb').read()
            after = open(out, 'rb').read()
            self.assertEqual(struct.unpack_from('<I', after, 0x3C)[0],
                             struct.unpack_from('<I', before, 0x3C)[0] + 8)
            _s0, raw0 = AC.segments(before)
            _s1, raw1 = AC.segments(after)
            self.assertEqual(raw0[1], raw1[1])
            self.assertEqual(raw0[2], raw1[2])
            for va in (P.EXIT_HOOK, P.LATE_HOOK) + P.START_HOOKS:
                self.assertEqual(struct.unpack_from('<I', raw1[0], va)[0]
                                 & 0xFC000000, 0x14000000)
            self.assertEqual(raw1[0][P.CATCH_UP_START:P.CATCH_UP_START + 4],
                             raw0[0][P.CATCH_UP_START:P.CATCH_UP_START + 4],
                             'the catch-up frame start must stay stock')
            self.assertEqual(r['slack'], 39091)
            self.assertEqual(r['mode'], 'hybrid')
            self.assertEqual(len(r['start_entries']), 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class DefaultIsTheGateOnly(unittest.TestCase):
    @unittest.skipUnless(os.path.exists(DUMP_MAIN), 'needs the dumped main')
    def test_gate_only(self):
        tmp = tempfile.mkdtemp()
        try:
            out = os.path.join(tmp, 'main')
            r = P.apply_to_nso(DUMP_MAIN, out, 66)
            self.assertEqual(r['mode'], 'gate')
            before = open(DUMP_MAIN, 'rb').read()
            after = open(out, 'rb').read()
            self.assertEqual(struct.unpack_from('<I', after, 0x3C)[0],
                             struct.unpack_from('<I', before, 0x3C)[0],
                             'the gate needs no BSS')
            _s0, raw0 = AC.segments(before)
            _s1, raw1 = AC.segments(after)
            for va in (P.EXIT_HOOK,) + P.START_HOOKS:
                self.assertEqual(raw0[0][va:va + 4], raw1[0][va:va + 4])
            for va in (P.LATE_HOOK, P.BATTLE_LATE_HOOK):
                self.assertEqual(struct.unpack_from('<I', raw1[0], va)[0]
                                 & 0xFC000000, 0x14000000)
            import ff7nx_deadspace as DS
            lo, hi = DS.part(out, 'pace')
            for va in (r['late_entry'], r['battle_entry']):
                self.assertTrue(lo <= va < hi, hex(va))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_every_cave_is_branch_free(self):
        """BUILD 532: a windowed cave (b.cond/cbz) found no room on the
        finished module, and the pass was skipped on hardware."""
        from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
        md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
        addr = lambda i: 0x400000 + 4 * i
        for words in (P.build_late_cave(0x400000, addr, 77490.9),
                      P.build_start_cave(0x400000, addr, BSS, 39091),
                      P.build_exit_cave(0x400000, addr, BSS)):
            for i, w in enumerate(words):
                ins = list(md.disasm(struct.pack('<I', w), addr(i)))[0]
                self.assertNotIn(ins.mnemonic.split('.')[0],
                                 ('cbz', 'cbnz', 'tbz', 'tbnz', 'adr'))
                self.assertFalse(ins.mnemonic.startswith('b.'), ins.mnemonic)


class TheArithmetic(unittest.TestCase):
    """A model of both limiters over a run of frames, in milliseconds."""
    T66 = (19.2e6 / 66 - 10000) / 19.2e6 * 1000.0      # 14.63 ms
    T60 = 1000.0 / 60.0

    def periods(self, logic, flip, hybrid, frames=50):
        t, release, out = 0.0, None, []
        for _ in range(frames):
            start = t
            deadline = start + self.T66
            if hybrid and release is not None:
                deadline = min(deadline, release + self.T60)
            t = max(start + logic, deadline)          # the limiter releases
            if release is not None:
                out.append(t - release)
            release = t
            t += flip                                 # the tail
        return out[5:]

    def test_a_normal_frame_is_paced_exactly_as_stock(self):
        for flip in (0.5, 1.0, 2.0):
            self.assertEqual(self.periods(6, flip, False),
                             self.periods(6, flip, True))

    def test_a_long_flip_holds_60(self):
        stock = self.periods(8, 7, False)[-1]
        hybrid = self.periods(8, 7, True)[-1]
        self.assertAlmostEqual(stock, self.T66 + 7, places=6)   # ~46 fps
        self.assertAlmostEqual(hybrid, self.T60, places=6)      # 60


if __name__ == '__main__':
    unittest.main(verbosity=2)
