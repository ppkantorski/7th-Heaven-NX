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


# ------------------------------------------------------- BUILD 855: world
WNAMES = {FP.WORLD_HOUSE[0]: '666ddd', FP.WORLD_HOUSE[1]: '6656a4',
          FP.WORLD_HOUSE[2]: '666da3', FP.WORLD_HOUSE[3]: '666dc0',
          FP.WORLD_INPUT: '41a21e', FP.WORLD_UPDATE: '74db8c',
          FP.WORLD_AFTER[0]: '666df4', FP.WORLD_AFTER[1]: '6656d1',
          FP.WORLD_FLUSH: '660e95', FP.WORLD_SUBMIT: '66e641'}


def _run_world(mult, step=0, debt=1, flag=0, state=None, divisor=60.0,
               st=None, tap=None, land=0, exitflag=0):
    mem = E.Mem()
    mem.write(FP.BOOST_MULT, struct.pack('<i', mult))
    mem.write(FP.CTX_GOT, struct.pack('<Q', CTX_HOST))
    s = ESP0 - 20                      # 4 args + return slot, as at the site
    mem.write(CTX_HOST + 0x10, struct.pack('<II', s, EBP))
    s32(mem, EBP + 8, GAME_OBJ)
    mem.write(HOST + GAME_OBJ + 0x40, struct.pack('<d', 100.0))
    mem.write(HOST + FP.WORLD_COUNTER_STEP, struct.pack('<d', 1.0))
    s32(mem, FP.WORLD_DEBT_FLAG, debt)
    s32(mem, FP.WORLD_LIMIT_FLAG, 0 if debt else 1)
    s32(mem, FP.WORLD_DRAW_FLAG, flag)
    s32(mem, FP.WORLD_SUBMIT_ARG, 0x5150)
    mem.write(HOST + FP.WORLD_DIVISOR, struct.pack('<d', divisor))
    if state is not None and st is not None:
        mem.write(state, struct.pack('<4Q', *st))
    for i, v in enumerate(FP.WORLD_UPDATE_ARGS):
        s32(mem, s + 4 + 4 * i, v)
    if tap is not None:
        s32(mem, FP.WORLD_TAP_COUNT, tap)
    s32(mem, FP.WORLD_LAND_STATE, land)
    s32(mem, FP.WORLD_EXIT_FLAG, exitflag)
    calls = []
    skips = []

    def native(fn):
        def run(cpu):
            esp = struct.unpack('<I', mem.read(CTX_HOST + 0x10, 4))[0]
            args = tuple(g32(mem, esp + 4 + 4 * k) for k in range(4))
            calls.append((WNAMES[fn], esp, args, g32(mem, FP.WORLD_DRAW_FLAG)))
            mem.write(CTX_HOST + 0x10, struct.pack('<I', esp + 4))
            if fn == FP.WORLD_UPDATE:
                mem.write(CTX_HOST, struct.pack('<I', 0x77))
                if state is not None:
                    skips.append(struct.unpack('<Q', mem.read(
                        state + FP.WORLD_STATE_SKIP, 8))[0])
                if tap is not None:                 # 0x7667B2: button held
                    s32(mem, FP.WORLD_TAP_COUNT,
                        g32(mem, FP.WORLD_TAP_COUNT) + 1)
            for r in range(0, 19):
                cpu.x[r] = E.GARBAGE
        return run
    cpu = E.Cpu(mem, native={fn: native(fn) for fn in WNAMES})
    cpu.cntpct_step = step
    cpu.sp = 0x7000000
    for q in range(31):
        cpu.x[q] = 0x1000 + q
    site = FP.WORLD_SITE
    words = FP.build_world_boost_cave(CAVE, lambda i: CAVE + 4 * i, BUDGET,
                                      state=state)
    code = {site: A.bl(site, CAVE)}
    code.update({CAVE + 4 * i: w for i, w in enumerate(words)})
    before = list(cpu.x)
    end = cpu.run(site, code, start_pc=site)
    _run_world.skips = skips
    return mem, cpu, calls, end, before, s


def test_world_stock_at_1x():
    mem, cpu, calls, end, before, s = _run_world(1)
    assert end == FP.WORLD_UPDATE and cpu.x[30] == FP.WORLD_SITE + 4
    assert calls == [] and _esp(mem) == s
    assert g32(mem, FP.WORLD_DEBT_FLAG) == 1           # untouched
    assert g32(mem, FP.WORLD_DRAW_FLAG) == 0
    assert all(cpu.x[q] == before[q] for q in range(19, 30))


def test_world_two_no_draw_ticks_at_3x_then_the_drawing_one():
    mem, cpu, calls, end, before, s = _run_world(3)
    assert end == FP.WORLD_UPDATE and cpu.x[30] == FP.WORLD_SITE + 4
    assert cpu.sp == 0x7000000 and _esp(mem) == s
    names = [c[0] for c in calls]
    # 855b: no submission in an extra tick; the housekeeping (render-list
    # reset) runs after its update; 855c: the input for the NEXT tick last
    tick = ['74db8c', '666ddd', '6656a4', '666da3', '666dc0', '41a21e']
    assert names == tick * 2
    assert all(c[3] == 0 for c in calls)                # no draw in extras
    for c in calls:
        if c[0] == '74db8c':
            assert c[2] == FP.WORLD_UPDATE_ARGS and c[1] == s - 20
        elif c[0] == '41a21e':
            assert c[2][0] == 0
        else:
            assert c[2][0] == GAME_OBJ
    assert gd(mem, GAME_OBJ + 0x40) == 102.0
    assert g32(mem, FP.WORLD_RESULT) == 0x77
    assert g32(mem, FP.WORLD_DEBT_FLAG) == 0            # no catch-up pass
    assert g32(mem, FP.WORLD_LIMIT_FLAG) == 1
    assert g32(mem, FP.WORLD_DRAW_FLAG) == 1            # the frame draws
    assert all(cpu.x[q] == before[q] for q in range(19, 30))


def test_world_budget_and_cap():
    mem, cpu, calls, end, _b, s = _run_world(3, step=BUDGET)
    assert [c[0] for c in calls].count('74db8c') == 1
    assert end == FP.WORLD_UPDATE and _esp(mem) == s
    assert g32(mem, FP.WORLD_DRAW_FLAG) == 1
    mem, cpu, calls, end, _b, s = _run_world(100)
    assert [c[0] for c in calls].count('74db8c') == FP.BOOST_MAX_EXTRA


def test_world_sleep_sites_and_swapped_routine():
    import a64 as A2
    for va, word, kind, reg, loop, leave, _w, swap in FP.WORLD_SLEEP_SITES:
        enc = A2.cbz if kind == 'cbz' else A2.cbnz
        assert word == enc(reg, va, loop) and leave == va + 4
    r = FP.build_sleep_routine(0, lambda i: 4 * i, 0x100, 38400, 250000,
                               swap=True)
    assert r[0] == 0x1E603902                          # fsub d2, d8, d0
    assert FP.WORLD_PART_BYTES == __import__('ff7nx_fxinterp').WORLD_TOP


STATE = 0x3FF0000                      # module BSS in the flat emulator
T60 = FP.CPS // 60


def _state(mem):
    return struct.unpack('<5Q', mem.read(STATE, 40))


def test_world_controller_first_frame_and_hits():
    """855e: the first frame (no history) starts at budget T/2; the
    emulator's clock barely moves, so both extras fit; the extras flag is
    back to 0 for the frame's own tick."""
    mem, cpu, calls, end, before, s = _run_world_clock(
        (0, 0, 0, 0), 0, 60.0, 1 << 40)
    assert [c[0] for c in calls].count('74db8c') == 2
    prev, bud, _c, tick, extra = _state(mem)
    assert bud == T60 // 2 and prev == 1 << 40 and extra == 0
    assert end == FP.WORLD_UPDATE and _esp(mem) == s
    assert g32(mem, FP.WORLD_DRAW_FLAG) == 1
    assert all(cpu.x[q] == before[q] for q in range(19, 30))


def _run_world_clock(st, step, divisor, base, tap=None, land=0, exitflag=0):
    orig = E.Cpu.__init__

    def init(self, *a, **k):
        orig(self, *a, **k)
        self.cntpct = base
    E.Cpu.__init__ = init
    try:
        return _run_world(3, step=step, state=STATE, divisor=divisor, st=st,
                          tap=tap, land=land, exitflag=exitflag)
    finally:
        E.Cpu.__init__ = orig


def test_world_controller_backs_off_on_a_late_frame():
    late = T60 + T60 // 4
    mem, cpu, calls, end, _b, s = _run_world_clock(
        ((1 << 40) - late, 40000, 0, 1000), 0, 60.0, 1 << 40)
    assert _state(mem)[1] == 30000
    # never under T/16
    mem, cpu, calls, end, _b, s = _run_world_clock(
        ((1 << 40) - late, 20000, 0, 1000), 0, 60.0, 1 << 40)
    assert _state(mem)[1] == T60 // 16


def test_world_controller_grows_on_time_and_respects_the_tick_cost():
    on = T60
    mem, cpu, calls, end, _b, s = _run_world_clock(
        ((1 << 40) - on, 30000, 0, 0), 0, 60.0, 1 << 40)
    assert _state(mem)[1] == 30000 + T60 // 64
    assert [c[0] for c in calls].count('74db8c') == 2
    # capped at 5T/8
    mem, cpu, calls, end, _b, s = _run_world_clock(
        ((1 << 40) - on, 5 * T60 // 8, 0, 0), 0, 60.0, 1 << 40)
    assert _state(mem)[1] == T60 // 2 + T60 // 8
    # a remembered tick cost above the budget: no extra, and it fades
    mem, cpu, calls, end, _b, s = _run_world_clock(
        ((1 << 40) - on, 30000, 0, 80000), 0, 60.0, 1 << 40)
    assert [c[0] for c in calls].count('74db8c') == 0
    assert _state(mem)[3] == 80000 - 10000 and _state(mem)[4] == 0
    assert g32(mem, FP.WORLD_DRAW_FLAG) == 1 and end == FP.WORLD_UPDATE
    # 30 FPS world: T doubles
    mem, cpu, calls, end, _b, s = _run_world_clock(
        ((1 << 40) - 2 * T60, 30000, 0, 0), 0, 30.0, 1 << 40)
    assert _state(mem)[1] == 30000 + (2 * T60) // 64


def test_world_terrain_skipped_only_during_extras():
    for extra, want in ((0, 'call'), (1, 'skip'), (2, 'skip')):
        mem = E.Mem()
        mem.write(FP.CTX_GOT, struct.pack('<Q', CTX_HOST))
        mem.write(CTX_HOST + 0x10, struct.pack('<I', 0x7000))
        mem.write(STATE + FP.WORLD_STATE_SKIP, struct.pack('<Q', extra))
        site = FP.WORLD_TERRAIN_SITE
        words = FP.build_world_terrain_cave(CAVE, lambda i: CAVE + 4 * i,
                                            STATE)
        code = {site: A.bl(site, CAVE)}
        code.update({CAVE + 4 * i: w for i, w in enumerate(words)})
        cpu = E.Cpu(mem)
        cpu.sp = 0x7000000
        for q in range(31):
            cpu.x[q] = 0x1000 + q
        end = cpu.run(site, code, start_pc=site)
        esp = struct.unpack('<I', mem.read(CTX_HOST + 0x10, 4))[0]
        if want == 'call':
            assert end == FP.WORLD_TERRAIN and esp == 0x7000
            assert cpu.x[30] == site + 4
        else:
            assert end == site + 4 and esp == 0x7004
        assert all(cpu.x[q] == 0x1000 + q for q in range(0, 16))
def test_world_tap_counts_frames_not_ticks():
    """855f: the vehicle tap (0x7667B2, [0xE3A870] 1..14 on release) sees
    one count a frame: the two extras may start a tap (0 -> 1) but leave a
    running one alone; the frame's own tick then adds its 1."""
    for start, after_extras in ((0, 1), (1, 1), (5, 5), (14, 14)):
        mem, cpu, calls, end, before, s = _run_world_clock(
            (0, 0, 0, 0), 0, 60.0, 1 << 40, tap=start)
        assert [c[0] for c in calls].count('74db8c') == 2
        assert g32(mem, FP.WORLD_TAP_COUNT) == after_extras, start
        assert end == FP.WORLD_UPDATE and _esp(mem) == s
        assert all(cpu.x[q] == before[q] for q in range(19, 30))
    # a count the tick reset (released, or another path) is left as it is
    assert FP.WORLD_STATE_TAP + 4 <= FP.WORLD_STATE_TICK


def test_world_extras_run_the_land_step():
    """855g: an extra skips 0x75079D only with no land / exit under way;
    the mark is cleared for the frame's own tick."""
    for land, ex, want in ((0, 0, 1), (1, 0, 0), (2, 0, 0), (0, 1, 0),
                           (0, 2, 1), (2, 1, 0)):
        mem, cpu, calls, end, before, s = _run_world_clock(
            (0, 0, 0, 0), 0, 60.0, 1 << 40, land=land, exitflag=ex)
        assert _run_world.skips == [want, want], (land, ex)
        assert struct.unpack('<Q', mem.read(
            STATE + FP.WORLD_STATE_SKIP, 8))[0] == 0
        assert end == FP.WORLD_UPDATE and _esp(mem) == s
        assert all(cpu.x[q] == before[q] for q in range(19, 30))
    assert FP.WORLD_STATE_SKIP + 8 <= FP.WORLD_STATE_BYTES


def test_world_controller_encodings():
    import capstone
    cs = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    for w, want in ((FP._add_lsl64(13, 25, 25, 2), 'add x13, x25, x25, lsl #2'),
                    (FP._add_lsr64(13, 25, 25, 3), 'add x13, x25, x25, lsr #3'),
                    (FP._sub_lsr64(11, 23, 23, 2), 'sub x11, x23, x23, lsr #2'),
                    (FP._lsr64(13, 25, 2), 'lsr x13, x25, #2'),
                    (0x9E790059, 'fcvtzu x25, d2'),
                    (0x9E670121, 'fmov d1, x9')):
        i = next(cs.disasm(struct.pack('<I', w), 0))
        assert '%s %s' % (i.mnemonic, i.op_str) == want
