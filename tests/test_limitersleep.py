"""BUILD 842: the frame limiters sleep instead of spinning while the deadline
is more than SLEEP_MARGIN_MS away; every register the loops use survives.

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
import struct
import arm64emu as E
import ff7nx_fieldpace as FP

ROUTINE, STUB, SLEEP, LOOP, LEAVE = 0x100000, 0x100400, 0x200000, 0x300000, 0x300100
F = 280000.0


def _d(v):
    return struct.unpack('<Q', struct.pack('<d', v))[0]


def _code(kind, reg):
    margin = int(round(FP.SLEEP_MARGIN_MS * FP.CPS / 1000.0))
    r = FP.build_sleep_routine(ROUTINE, lambda i: ROUTINE + 4 * i, SLEEP,
                               margin, FP.SLEEP_QUANTUM_US * 1000)
    s = FP.build_sleep_stub(STUB, lambda i: STUB + 4 * i, kind, reg, LOOP,
                            LEAVE, ROUTINE)
    code = {}
    for base, words in ((ROUTINE, r), (STUB, s)):
        for i, w in enumerate(words):
            code[base + 4 * i] = w
    return code


def _run(kind, reg, go, d0, d8):
    calls = []

    def sleep(cpu):
        calls.append(cpu.x[0])
        for q in range(18):
            cpu.x[q] = 0xDEAD0000 + q
        for q in range(8):
            cpu.fp[q] = _d(-1.0)
    cpu = E.Cpu(E.Mem(), native={SLEEP: sleep})
    cpu.sp = 0x7000000
    for q in range(31):
        cpu.x[q] = 0x1000 + q
    cpu.x[reg] = go
    for q in range(16):
        cpu.fp[q] = _d(q + 0.5)
    cpu.fp[0], cpu.fp[8] = _d(d0), _d(d8)
    bx, bd = list(cpu.x), list(cpu.fp)
    end = cpu.run(ROUTINE, _code(kind, reg), start_pc=STUB)
    moved = [q for q in range(31) if cpu.x[q] != bx[q] and q not in (9, 30)]
    moved += ['d%d' % q for q in range(16) if cpu.fp[q] != bd[q] and q not in (2, 3)]
    return end, calls, moved, cpu.sp


def test_sleeps_only_when_far_and_restores():
    for kind, reg in (('cbz', 10), ('cbnz', 8)):
        go = 0 if kind == 'cbz' else 1
        for d8, want in ((100000.0, [FP.SLEEP_QUANTUM_US * 1000]),
                         (F - 20000.0, []), (F + 5000.0, [])):
            end, calls, moved, sp = _run(kind, reg, go, F, d8)
            assert (end, calls, moved, sp) == (LOOP, want, [], 0x7000000)
        end, calls, moved, sp = _run(kind, reg, go, float('nan'), 0.0)
        assert (end, calls) == (LOOP, [])
        end, calls, moved, sp = _run(kind, reg, 1 - go, F, 100000.0)
        assert (end, calls, moved) == (LEAVE, [], [])


def test_sites_and_toggle(monkeypatch):
    assert len(FP.SLEEP_SITES) == 4
    monkeypatch.setenv(FP.SLEEP_ENV, 'off')
    assert not FP.sleep_enabled()
    monkeypatch.delenv(FP.SLEEP_ENV)
    assert FP.sleep_enabled()
