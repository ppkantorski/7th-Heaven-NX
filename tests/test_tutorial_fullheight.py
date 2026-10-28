"""BUILD 845: the materia tutorial test (mds7pb_1 + a 4:3-or-taller movie) that
lets ff7nx_moviealign and ff7nx_moviebars stand aside for that movie only.

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
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src', 'integrations', 'echo_s'))
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src', 'widescreen', 'fmv'))
import a64 as A
import arm64emu
import echo_s_tutorial as T

BASE, HIT, MISS = 0x200000, 0x210000, 0x210100


def _run(field, w, h, renderer=True):
    words = T.tutorial_test(lambda i: BASE + 4 * i, 0, MISS)
    assert len(words) == T.TUTORIAL_TEST_WORDS
    words = words + [A.b(BASE + 4 * len(words), HIT)]
    code = {BASE + 4 * i: x for i, x in enumerate(words)}
    mem = arm64emu.Mem()
    cpu = arm64emu.Cpu(mem)
    mem.setu(cpu.guest_to_host(T.CURRENT_FIELD_ID), field, 2)
    if renderer:
        mem.setu(T.MOVIE_RENDERER_GOT, 0x84000000, 8)
        mem.setu(0x84000000, 0x83000000, 8)
        mem.setu(0x83000000 + T.RENDERER_W, w, 4)
        mem.setu(0x83000000 + T.RENDERER_H, h, 4)
    for q in range(19, 30):
        cpu.x[q] = 0x1000 + q
    end = cpu.run(BASE, None, code=code, start_pc=BASE)
    assert all(cpu.x[q] == 0x1000 + q for q in range(19, 30))
    return end


def test_only_the_tutorial_hits():
    f = T.MDS7PB_1_FIELD_ID
    assert _run(f, 1280, 960) == HIT
    assert _run(f, 1280, 1024) == HIT           # padded decoder texture
    assert _run(f, 1280, 896) == MISS           # every 10:7 FMV
    assert _run(f, 1276, 896) == MISS
    assert _run(0x50, 1280, 960) == MISS        # any other field
    assert _run(f, 0, 0) == MISS
    assert _run(f, 1280, 960, renderer=False) == MISS


def test_caves_carry_it_only_when_enabled(monkeypatch):
    import ff7nx_moviealign as MA
    with_t = MA.fill_cave(0.75, tutorial=True)(0x1000, lambda i: 0x1000 + 4 * i)
    without = MA.fill_cave(0.75, tutorial=False)(0x1000, lambda i: 0x1000 + 4 * i)
    assert len(with_t) - len(without) == T.TUTORIAL_TEST_WORDS + 12
    assert without[:10] == MA.cave_body() and with_t[:10] == MA.cave_body()
    monkeypatch.setenv(T.FULLVIEW_ENV, 'off')
    assert not T.fullview_enabled() and not MA.tutorial_enabled()
