#!/usr/bin/env python3
"""tests/test_arm64emu_xzr.py -- BUILD 280.

arm64emu mapped register 31 to SP in the 64-bit `subs`/`csel` decoders, where
the architecture says XZR. Every 64-bit clamp built on `cmp Xn, xzr` /
`csel Xd, xzr, Xn, lt` was therefore VERIFIED WRONG -- the emulator read the
stack pointer as the comparison operand and selected it as the result. The
projective remap is the first cave to use that pattern, and it is how this was
caught.

These tests pin the architectural behaviour so it cannot regress.

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
import sys

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import a64 as A                                                # noqa: E402
import arm64emu                                                # noqa: E402

M64 = (1 << 64) - 1
SP = 0x60000


def _run(words, pre):
    cpu = arm64emu.Cpu(arm64emu.Mem())
    cpu.sp = SP
    for r, v in pre.items():
        cpu.set(r, v & M64)
    cpu.run(0x1000, words)
    return cpu


def test_cmp_xn_xzr_reads_zero_not_the_stack_pointer():
    for v, want_lt in ((632, 0), (-5, 1), (0, 0), (1 << 40, 0)):
        cpu = _run([A.cmp_reg64(14, A.XZR)], {14: v})
        assert cpu.n ^ cpu.v == want_lt, (v, cpu.n, cpu.v)


def test_csel_from_xzr_selects_zero_not_the_stack_pointer():
    for v in (632, -5, 0, 1 << 40, SP + 1):
        cpu = _run([A.cmp_reg64(14, A.XZR), A.csel64(14, A.XZR, 14, 11)],
                   {14: v})
        assert arm64emu.s64(cpu.get(14)) == max(0, arm64emu.s64(v & M64))


def test_the_full_clamp_pattern_matches_python():
    lo_hi = [(0, 639), (0, 479)]
    for lo, hi in lo_hi:
        for v in (-(1 << 20), -1, 0, 1, 100, hi - 1, hi, hi + 1, 1 << 30):
            cpu = _run([A.cmp_reg64(14, A.XZR),
                        A.csel64(14, A.XZR, 14, 11),
                        A.movz(17, hi),
                        A.cmp_reg64(14, 17),
                        A.csel64(14, 17, 14, 12)], {14: v})
            assert cpu.get(14) == max(lo, min(hi, v)), (lo, hi, v, cpu.get(14))


def test_cmp_never_writes_the_stack_pointer():
    """`cmp Xn, Xm` is `subs xzr, Xn, Xm`. Writing the result to SP would
    corrupt the frame of whatever function the cave is borrowing."""
    cpu = _run([A.cmp_reg64(14, 17)], {14: 5, 17: 9})
    assert cpu.get_sp() == SP


def test_sub_xd_xn_xm_is_sixty_four_bit():
    cpu = _run([0xCB020029], {1: 1 << 40, 2: 7})       # sub x9, x1, x2
    assert cpu.get(9) == (1 << 40) - 7
