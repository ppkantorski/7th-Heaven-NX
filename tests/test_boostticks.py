"""BUILD 847b: at 3x every field frame runs two extra ticks before its one
draw_everything (+0x94B898), every battle frame two extra no-draw ticks before its own
66c3b2/update; at 1x every cave is the stock call.

Each cave runs in the emulator on its real words, with the game functions
it calls stood in by natives that record the emulated x86 esp and the
argument at [esp+4], pop their return slot as the translated callees do,
and clobber x0..x18 (AAPCS).

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
import a64 as A
import arm64emu as E
import ff7nx_fieldpace as FP

CAVE = 0x99A000
CTX = 0x5000                     # guest address of nothing; ctx is host-side
CTX_HOST = 0x60000000
HOST = 0x700000000               # arm64emu's flat guest -> host base
EBP, ESP0, GAME_OBJ = 0x8000, 0x7F00, 0x20000
BUDGET = 76800
NAMES = {FP.FIELD_BEGIN2: 'begin2', FP.FIELD_RESET: 'reset',
         FP.FIELD_BEGIN: 'begin', FP.FIELD_UPDATE: 'update',
         FP.SET_DRAW: 'setdraw', FP.BATTLE_UPDATE: 'bupdate'}


def g32(mem, va):
    return struct.unpack('<I', mem.read(HOST + va, 4))[0]


def s32(mem, va, v):
    mem.write(HOST + va, struct.pack('<I', v & 0xFFFFFFFF))


def gd(mem, va):
    return struct.unpack('<d', mem.read(HOST + va, 8))[0]


def _setup(mult, movie=0, eax=1, exit_flag=0, debt=1):
    mem = E.Mem()
    mem.write(FP.BOOST_MULT, struct.pack('<i', mult))
    mem.write(FP.CTX_GOT, struct.pack('<Q', CTX_HOST))
    mem.write(FP.MOVIE_GOT, struct.pack('<Q', 0x61000000))
    mem.write(0x61000000, struct.pack('<Q', 0x62000000))
    mem.write(0x62000000 + FP.MOVIE_IS_PLAYING, struct.pack('<I', movie))
    mem.write(CTX_HOST + 0x10, struct.pack('<II', ESP0, EBP))
    s32(mem, EBP + 8, GAME_OBJ)
    mem.write(HOST + GAME_OBJ + 0x40, struct.pack('<d', 100.0))
    mem.write(HOST + FP.FIELD_COUNTER_STEP, struct.pack('<d', 1.0))
    mem.write(HOST + FP.BATTLE_COUNTER_STEP, struct.pack('<d', 1.0))
    s32(mem, FP.FIELD_DEBT_FLAG, debt)
    s32(mem, FP.BATTLE_DEBT_FLAG, debt)
    s32(mem, FP.FIELD_EXIT_FLAG, exit_flag)
    calls = []

    def native(fn):
        def run(cpu):
            esp = struct.unpack('<I', mem.read(CTX_HOST + 0x10, 4))[0]
            calls.append((NAMES[fn], esp, g32(mem, esp + 4)))
            mem.write(CTX_HOST + 0x10, struct.pack('<I', esp + 4))
            if fn == FP.FIELD_UPDATE:
                mem.write(CTX_HOST, struct.pack('<I', eax))
            for r in range(0, 19):
                cpu.x[r] = E.GARBAGE
        return run
    cpu = E.Cpu(mem, native={fn: native(fn) for fn in NAMES})
    cpu.sp = 0x7000000
    for q in range(31):
        cpu.x[q] = 0x1000 + q
    return mem, cpu, calls


def _run_field(mult, step=0, **kw):
    """Entered at +0x94B898 as stock: draw's argument pushed, its return
    slot reserved -- esp = S = ESP0 - 8."""
    mem, cpu, calls = _setup(mult, **kw)
    cpu.cntpct_step = step
    site = FP.FIELD_SITE
    words = FP.build_field_boost_cave(CAVE, lambda i: CAVE + 4 * i, BUDGET)
    code = {site: A.bl(site, CAVE)}
    code.update({CAVE + 4 * i: w for i, w in enumerate(words)})
    s32(mem, ESP0 - 4, GAME_OBJ)
    mem.write(CTX_HOST + 0x10, struct.pack('<I', ESP0 - 8))
    before = list(cpu.x)
    end = cpu.run(site, code, start_pc=site)
    return mem, cpu, calls, end, before


def _esp(mem):
    return struct.unpack('<I', mem.read(CTX_HOST + 0x10, 4))[0]


S = ESP0 - 8
TICK = [('reset', S - 4), ('begin', S - 8), ('begin2', S - 4),
        ('update', S - 8)]


def test_field_three_ticks_at_3x_then_the_draw():
    mem, cpu, calls, end, before = _run_field(3)
    assert end == FP.FIELD_DRAW and cpu.x[30] == FP.FIELD_SITE + 4
    assert cpu.sp == 0x7000000
    assert [c[:2] for c in calls] == TICK * 2
    assert all(c[2] == GAME_OBJ for c in calls if c[0] in ('begin',
                                                           'update'))
    assert _esp(mem) == S                         # the draw's own frame
    assert gd(mem, GAME_OBJ + 0x40) == 102.0
    assert g32(mem, FP.FIELD_DEBT_FLAG) == 0
    assert all(cpu.x[q] == before[q] for q in range(19, 30))


def test_field_stock_at_1x():
    mem, cpu, calls, end, before = _run_field(1)
    assert end == FP.FIELD_DRAW and cpu.x[30] == FP.FIELD_SITE + 4
    assert calls == [] and _esp(mem) == S
    assert g32(mem, FP.FIELD_DEBT_FLAG) == 1
    assert gd(mem, GAME_OBJ + 0x40) == 100.0
    assert all(cpu.x[q] == before[q] for q in range(19, 30))


def test_field_movie_exit_and_budget():
    mem, cpu, calls, end, _b = _run_field(3, movie=1)
    assert end == FP.FIELD_DRAW and calls == [] and _esp(mem) == S
    assert g32(mem, FP.FIELD_DEBT_FLAG) == 0
    for kw in ({'eax': 0}, {'exit_flag': 1}):
        mem, cpu, calls, end, before = _run_field(3, **kw)
        assert [c[0] for c in calls] == [t[0] for t in TICK]
        assert end == FP.FIELD_EPILOGUE and cpu.sp == 0x7000000
        assert all(cpu.x[q] == before[q] for q in range(19, 30))
    mem, cpu, calls, end, _b = _run_field(3, step=BUDGET)
    assert [c[0] for c in calls].count('update') == 1
    assert end == FP.FIELD_DRAW and _esp(mem) == S


def test_field_capped():
    mem, cpu, calls, end, _b = _run_field(100)
    assert [c[0] for c in calls].count('update') == FP.BOOST_MAX_EXTRA


def _run_battle(mult, normal, debt=1):
    mem, cpu, calls = _setup(mult, debt=debt)
    site = 0x90568 if normal else 0x90500
    words = FP.build_battle_boost_cave(CAVE, lambda i: CAVE + 4 * i, BUDGET)
    code = {site: A.bl(site, CAVE + FP.BATTLE_ENTRY[normal])}
    code.update({CAVE + 4 * i: w for i, w in enumerate(words)})
    flag = 0 if (normal and debt) else 1          # what the stock pushed
    s32(mem, ESP0 - 4, flag)
    mem.write(CTX_HOST + 0x10, struct.pack('<I', ESP0 - 8))
    before = list(cpu.x)
    end = cpu.run(site, code, start_pc=site)
    return mem, cpu, calls, end, before, site


def test_battle_three_ticks_at_3x():
    for normal in (True, False):
        mem, cpu, calls, end, before, site = _run_battle(3, normal)
        assert end == FP.SET_DRAW and cpu.x[30] == site + 4
        assert cpu.sp == 0x7000000
        tick = [('setdraw', ESP0 - 16, 0), ('bupdate', ESP0 - 16, GAME_OBJ)]
        assert calls == tick * 2
        assert _esp(mem) == ESP0 - 8              # the stock call's frame
        assert g32(mem, ESP0 - 4) == 1            # the frame's tick draws
        assert gd(mem, GAME_OBJ + 0x40) == 102.0
        assert g32(mem, FP.BATTLE_DEBT_FLAG) == (0 if normal else 1)
        assert all(cpu.x[q] == before[q] for q in range(19, 30))


def test_battle_stock_at_1x():
    for normal in (True, False):
        mem, cpu, calls, end, before, site = _run_battle(1, normal)
        assert end == FP.SET_DRAW and cpu.x[30] == site + 4 and calls == []
        assert g32(mem, FP.BATTLE_DEBT_FLAG) == 1
        assert g32(mem, ESP0 - 4) == (0 if normal else 1)   # untouched
        assert all(cpu.x[q] == before[q] for q in range(19, 30))


def test_sites_are_stock_calls_and_toggle(monkeypatch):
    assert FP.FIELD_SITE == 0x94B898
    monkeypatch.delenv(FP.BOOST_ENV, raising=False)
    assert FP.boost_enabled()
    monkeypatch.setenv(FP.BOOST_ENV, 'off')
    assert not FP.boost_enabled()
    monkeypatch.setenv(FP.BOOST_MS_ENV, '2')
    assert FP.boost_budget_counts() == 38400


def test_battle_menu_passes_follow_the_multiplier():
    """BUILD 847c: the menu loop count ([ebp-0x18], ATB) is k x mult."""
    assert FP.menu_site_count(0x52800028) == 1          # 60 FPS: mov w8, #1
    assert FP.menu_site_count(0x52800048) == 2          # 30 FPS
    assert FP.menu_site_count(FP.MENU_ORR4) == 4        # vanilla
    assert FP.menu_site_count(0x52800029) is None       # not w8
    for k in (1, 2):
        for mult, want in ((1, 1), (3, 3), (0, 1), (-7, 1), (2, 2),
                           (100, 1 + FP.BOOST_MAX_EXTRA)):
            mem = E.Mem()
            mem.write(FP.BOOST_MULT, struct.pack('<i', mult))
            site = FP.MENU_SITE
            words = FP.build_menu_boost_cave(CAVE, lambda i: CAVE + 4 * i, k)
            code = {site: A.bl(site, CAVE)}
            code.update({CAVE + 4 * i: w for i, w in enumerate(words)})
            cpu = E.Cpu(mem)
            cpu.sp = 0x7000000
            for q in range(31):
                cpu.x[q] = 0x1000 + q
            before = list(cpu.x)
            end = cpu.run(site, code, start_pc=site)
            assert end == site + 4 and cpu.x[8] == want * k
            assert all(cpu.x[q] == before[q] for q in range(31)
                       if q not in (8, 9, 30))
