"""BUILD 877: the cutscene movie wait calls present() and then sleeps one
quantum, instead of spinning (measured: 13,300 passes a second, every
present back in under 1 ms). Callee-saved registers and sp survive.

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
import arm64emu as E
import ff7nx_moviepace as MP

CAVE, SLEEP, RET = 0x100000, 0x200000, 0x300000


def test_877_present_then_one_quantum_of_sleep():
    calls = []

    def present(cpu):
        calls.append('present')
        for q in range(19):
            cpu.x[q] = 0xDEAD0000 + q

    def sleep(cpu):
        calls.append(('sleep', cpu.x[0]))
        for q in range(19):
            cpu.x[q] = 0xBEEF0000 + q

    words = MP.wait_sleep_cave(SLEEP)(CAVE, lambda i: CAVE + 4 * i)
    code = {CAVE + 4 * i: w for i, w in enumerate(words)}
    cpu = E.Cpu(E.Mem(), native={MP.PRESENT: present, SLEEP: sleep})
    cpu.sp = 0x7000000
    for q in range(31):
        cpu.x[q] = 0x1000 + q
    cpu.x[30] = RET
    before = list(cpu.x)
    end = cpu.run(CAVE, code, start_pc=CAVE)
    assert calls == ['present', ('sleep', MP.WAIT_SLEEP_US * 1000)]
    assert end == RET
    assert cpu.sp == 0x7000000
    assert [cpu.x[q] for q in range(19, 30)] == before[19:30]


if __name__ == '__main__':
    test_877_present_then_one_quantum_of_sleep()
    print('ok')
