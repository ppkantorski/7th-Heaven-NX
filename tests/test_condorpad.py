"""BUILDs 569-570: Fort Condor cursor and placement line (ff7nx_condorpad).

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
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import a64 as A  # noqa: E402
import arm64emu as E  # noqa: E402
import ff7nx_analog as AN  # noqa: E402
import ff7nx_condorpad as P  # noqa: E402
import nxmap  # noqa: E402

MAIN = os.path.join(HERE, 'dump', 'exefs', 'main')
SP = 0x7F000000
REG = 0x6300000
ESP = 0x19F000
CHAIN = (0x6400000, 0x6400100, 0x6400200, 0x6400300)
OBJ = 0x6400400


def _f(v):
    return struct.unpack('<I', struct.pack('<f', v))[0]


_CACHE = {}


def _patched(spd=0.75):
    if spd not in _CACHE:
        d = tempfile.mkdtemp()
        try:
            dest = os.path.join(d, 'main')
            info = P.apply_to_nso(MAIN, dest, stock=MAIN, spd=spd)
            _CACHE[spd] = (nxmap.Main(dest).text, info['bss'])
        finally:
            shutil.rmtree(d)
    return _CACHE[spd]


def _target(t, site):
    w = struct.unpack_from('<I', t, site)[0]
    assert w >> 26 == 0x25, hex(w)                       # bl
    imm = w & 0x3FFFFFF
    return site + 4 * (imm - (1 << 26) if imm & (1 << 25) else imm)


def _code(t, entry):
    code, va = {}, entry
    while True:
        w = struct.unpack_from('<I', t, va)[0]
        code[va] = w
        if w == A.b(va, P.STEP_FN):
            return code
        va += 4


class Rig:
    def __init__(self, spd=0.75):
        self.t, self.bss = _patched(spd)
        self.entry = _target(self.t, P.CALL_SITES[0][0])
        self.code = _code(self.t, self.entry)
        self.mem = E.Mem()
        self.cpu = E.Cpu(self.mem, paged=True)
        m = self.mem
        m.setu(P.REGFILE_GOT, REG, 8)
        for o in range(0, P.BSS_BYTES, 4):            # BSS is zero-filled
            m.setu(self.bss + o, 0, 4)
        m.setu(REG + 0x10, ESP, 4)
        m.setu(AN.INPUT_GOT, CHAIN[0], 8)
        ptrs = list(CHAIN[1:]) + [OBJ]
        for (base, off), nxt in zip(zip(CHAIN, AN.INPUT_CHAIN), ptrs):
            m.setu(base + off, nxt, 8)
        self.stick(0.0, 0.0)

    def stick(self, x, y):
        m = self.mem
        m.setu(OBJ + AN.OBJ_RIGHT, _f(max(x, 0.0)), 4)
        m.setu(OBJ + AN.OBJ_LEFT, _f(max(-x, 0.0)), 4)
        m.setu(OBJ + AN.OBJ_UP, _f(max(y, 0.0)), 4)
        m.setu(OBJ + AN.OBJ_DOWN, _f(max(-y, 0.0)), 4)

    def step(self, mask):
        cpu, m = self.cpu, self.mem
        g = cpu.guest_to_host
        m.setu(g(ESP + 4), mask, 4)
        for r in (19, 20, 21, 22, 29):
            cpu.x[r] = 0x1000 + r
        cpu.x[30] = 0xDEAD0
        cpu.sp = SP
        out = cpu.run(self.entry, self.code)
        assert out == P.STEP_FN
        assert [cpu.x[r] for r in (19, 20, 21, 22, 29, 30)] == \
            [0x1000 + r for r in (19, 20, 21, 22, 29)] + [0xDEAD0]
        assert cpu.sp == SP
        return m.u(g(ESP + 4), 4)


def test_all_four_calls_go_through_one_cave_and_nothing_else_moves():
    import ff7nx_deadspace as DS
    t0 = nxmap.Main(MAIN).text
    t1, _bss = _patched()
    lo, hi = DS.part(MAIN, 'condorpad')
    entries = {_target(t1, s) for s, _w in P.CALL_SITES}
    assert len(entries) == 1 and lo <= entries.pop() < hi
    sites = ({s for s, _w in P.CALL_SITES} | {P.LINE_SITE}
             | {P.BANNER_SITE, P.BANNER_BRANCH}
             | {va for sites in P.MSG_SITES for va in sites}
             | {P.REPORT_SITE}
             | {va for b in P.BUTTON_SITES for va in (b[0], b[2])})
    ch = [va for va in range(0, len(t0), 4) if t0[va:va + 4] != t1[va:va + 4]]
    assert all(va in sites or lo <= va < hi for va in ch)


def test_no_direction_is_passed_through():
    r = Rig()
    for mask in (0, 0x80, 0x20, 0x0F0F):
        assert r.step(mask) == mask


def test_dpad_keeps_its_bits_at_three_quarter_speed():
    r = Rig()
    r.step(0)                                   # released: gate primed
    moved = 0
    for _ in range(400):
        out = r.step(0x2080)                    # right + 0x80
        assert out & 0x0FFF == 0x80             # other bits untouched
        assert out & 0xF000 in (0, 0x2000)
        moved += out == 0x2080
    assert moved == 300


def test_first_step_after_a_press_moves():
    r = Rig()
    r.step(0)
    assert r.step(0x1000) == 0x1000


def test_full_speed_is_stock():
    r = Rig(1.0)
    assert all(r.step(0x4000) == 0x4000 for _ in range(50))


def test_stick_any_angle_true_slope_constant_speed():
    r = Rig(1.0)
    for deg in (0, 10, 30, 45, 60, 80, 135, 200, 290, 350):
        rad = math.radians(deg)
        x, y = math.cos(rad), math.sin(rad)
        r.stick(x, y)
        r.step(0)
        # the port's own 8-way bits for this tilt (any set is fine: the
        # cave replaces them)
        mask = 0x2000 if x > 0.1 else 0x8000 if x < -0.1 else 0
        mask |= 0x1000 if y > 0.1 else 0x4000 if y < -0.1 else 0
        dx = dy = 0
        n = 600
        for _ in range(n):
            out = r.step(mask | 0x80)
            assert out & 0x0FFF == 0x80
            dx += (1 if out & 0x2000 else 0) - (1 if out & 0x8000 else 0)
            dy += (1 if out & 0x1000 else 0) - (1 if out & 0x4000 else 0)
        assert abs(dx - n * x) <= 2 and abs(dy - n * y) <= 2, (deg, dx, dy)


def test_small_tilt_leaves_the_port_bits():
    r = Rig(1.0)
    r.stick(0.1, 0.05)                         # inside the dead zone
    assert r.step(0x2000) == 0x2000


def _line_run(x, w, y=250):
    t, _bss = _patched()
    entry = _target(t, P.LINE_SITE)
    code, va = {}, entry
    while True:
        word = struct.unpack_from('<I', t, va)[0]
        code[va] = word
        if word == A.b(va, P.RECT_FN):
            break
        va += 4
    mem = E.Mem()
    cpu = E.Cpu(mem, paged=True)
    g = cpu.guest_to_host
    mem.setu(P.REGFILE_GOT, REG, 8)
    mem.setu(REG + 0x10, ESP, 4)
    for off, v in ((4, x), (8, y), (0xC, w), (0x10, 2), (0x14, 0xA0FF0000)):
        mem.setu(g(ESP + off), v & 0xFFFFFFFF, 4)
    for r in (19, 20, 21, 22, 29):
        cpu.x[r] = 0x1000 + r
    cpu.x[30] = 0xDEAD0
    cpu.sp = SP
    assert cpu.run(entry, code) == P.RECT_FN
    assert [cpu.x[r] for r in (19, 20, 21, 22, 29, 30)] == \
        [0x1000 + r for r in (19, 20, 21, 22, 29)] + [0xDEAD0]
    assert cpu.sp == SP
    got = [E.s32(mem.u(g(ESP + off), 4)) for off in (4, 8, 0xC, 0x10)]
    return got, mem.u(g(ESP + 0x14), 4)


def test_line_spans_the_16_9_width():
    (x, y, w, h), col = _line_run(0, 640)
    assert (x, y, w, h, col) == (-107, 250, 854, 2, 0xA0FF0000)
    # 640-unit space, 0.75 in the shader: -107 and 747 are past both edges
    assert x <= -106.67 and x + w >= 746.67


def test_line_keeps_other_viewports():
    assert _line_run(0xA0, 320)[0] == [0xA0, 250, 320, 2]
    assert _line_run(0xA0, 640)[0] == [0xA0 - 107, 250, 854, 2]


def test_line_only_and_pad_only_install_independently():
    import ff7nx_deadspace as DS
    t0 = nxmap.Main(MAIN).text
    for pad, line in ((True, False), (False, True)):
        d = tempfile.mkdtemp()
        try:
            dest = os.path.join(d, 'main')
            P.apply_to_nso(MAIN, dest, stock=MAIN, spd=0.75, pad=pad,
                           line=line, banner=P.BANNER_STOCK_Y,
                           msg=P.MSG_STOCK_Y, buttons=False)
            t1 = nxmap.Main(dest).text
        finally:
            shutil.rmtree(d)
        lo, hi = DS.part(MAIN, 'condorpad')
        ch = {va for va in range(0, len(t0), 4)
              if t0[va:va + 4] != t1[va:va + 4] and not lo <= va < hi}
        want = set()
        if pad:
            want |= {s for s, _w in P.CALL_SITES}
        if line:
            want.add(P.LINE_SITE)
        assert ch == want


def _slide(t, start):
    """Run the banner slide block until it stops; (frames, final value)."""
    code = {va: struct.unpack_from('<I', t, va)[0]
            for va in range(0x8E386C, 0x8E38B0, 4)}
    mem = E.Mem()
    cpu = E.Cpu(mem, paged=True)
    g = cpu.guest_to_host
    mem.setu(g(0xC72DFC), start & 0xFFFF, 2)
    for n in range(1, 400):
        cpu.x[19], cpu.x[21] = 0xC72DF0, REG
        cpu.sp = SP
        out = cpu.run(0x8E386C, code)
        assert out in (0x8E38B0, 0x8E3B1C), hex(out)
        raw = mem.u(g(0xC72DFC), 2)
        v = raw - 0x10000 if raw & 0x8000 else raw
        if out == 0x8E38B0:
            return n, v
    raise AssertionError('never stopped from %d' % start)


def test_banner_stops_30_units_lower():
    t0 = nxmap.Main(MAIN).text
    t1, _bss = _patched()
    assert struct.unpack_from('<I', t1, P.BANNER_SITE)[0] == P.banner_word(6)
    for start in (0x40, 0x30, 0x60, 0xB0):
        n0, v0 = _slide(t0, start)
        n1, v1 = _slide(t1, start)
        assert (v0, v1) == (-24, 6)
        assert n0 - n1 == 15                     # 30 units, 2 a frame
    # a start at/below the new stop no longer runs away
    assert _slide(t1, 4) == (1, 2)


def test_banner_word_round_trips_stock():
    assert P.banner_word(P.BANNER_STOCK_Y) == P.BANNER_WORD
    for bad in ('5', '100'):
        try:
            P.banner_y({P.BANNER_ENV: bad})
        except ValueError:
            continue
        raise AssertionError(bad)


# ------------------------------------------- BUILD 575: message stop, B/A
def _run_block(t, lo, hi, setup):
    code = {va: struct.unpack_from('<I', t, va)[0] for va in range(lo, hi, 4)}
    mem = E.Mem()
    cpu = E.Cpu(mem, paged=True)
    setup(cpu, mem)
    cpu.sp = SP
    out = cpu.run(lo, code)
    return cpu, mem, out


def _clamp(t, lo, hi, value):
    def setup(cpu, mem):
        mem.setu(cpu.guest_to_host(0xC72DFC), value & 0xFFFF, 2)
        cpu.x[19], cpu.x[21] = 0xC72DFC, REG
    cpu, mem, _out = _run_block(t, lo, hi, setup)
    raw = mem.u(cpu.guest_to_host(0xC72DFC), 2)
    return raw - 0x10000 if raw & 0x8000 else raw


def test_message_pair_stops_18_units_higher_in_both_copies():
    t0 = nxmap.Main(MAIN).text
    t1, _bss = _patched()
    for lo, hi in ((0x8E4EE0, 0x8E4F28), (0x8E4D00, 0x8E4D48)):
        for v, stock, ours in ((-32, -32, -32), (0, 0, 0), (29, 29, 29),
                               (30, 30, 30), (31, 31, 30), (63, 63, 30),
                               (64, 64, 30), (70, 64, 30)):
            assert _clamp(t0, lo, hi, v) == stock, (hex(lo), v)
            assert _clamp(t1, lo, hi, v) == ours, (hex(lo), v)


def test_start_combat_still_lands_on_its_stop_from_the_message_stop():
    t1, _bss = _patched()
    assert (P.MSG_Y - P.BANNER_Y) % 2 == 0
    assert _slide(t1, P.MSG_Y) == ((P.MSG_Y - P.BANNER_Y) // 2, P.BANNER_Y)


# ------------------------------------------- BUILD 576: the battle report
def _report_frames(t, target, start=-32):
    """Run x86 0x5FCA52 (ARM +0x8E5160..+0x8E51F8) frame by frame with
    [0xC625DC] = target until it clears the state; (frames, final)."""
    lo, hi = 0x8E5160, 0x8E51F8
    code = {va: struct.unpack_from('<I', t, va)[0] for va in range(lo, hi, 4)}
    mem = E.Mem()
    cpu = E.Cpu(mem, paged=True)
    g = cpu.guest_to_host
    mem.setu(g(0xC72DFC), start & 0xFFFF, 2)
    mem.setu(g(0xC625DC), target & 0xFFFF, 2)
    mem.setu(g(0xC625E0), 0x11, 4)
    for n in range(1, 200):
        cpu.x[19], cpu.x[20], cpu.x[21] = 0xC625DC, 0xC72DFC, REG
        cpu.sp = SP
        assert cpu.run(lo, code) == hi
        raw = mem.u(g(0xC72DFC), 2)
        v = raw - 0x10000 if raw & 0x8000 else raw
        if mem.u(g(0xC625E0), 4) == 0:
            return n, v
    raise AssertionError('never stopped')


def _report_push(t):
    cpu = E.Cpu(E.Mem(), paged=True)
    cpu.sp = SP
    va = P.REPORT_SITE
    assert cpu.run(va, {va: struct.unpack_from('<I', t, va)[0]}) == va + 4
    return cpu.x[8] & 0xFFFFFFFF


def test_battle_report_stops_18_units_higher():
    t0 = nxmap.Main(MAIN).text
    t1, _bss = _patched()
    assert _report_push(t0) == 0x30 and _report_push(t1) == P.MSG_Y == 30
    for a, want, _n in P.REPORT_ANCHORS:
        assert struct.unpack_from('<I', t1, a)[0] == want
    assert _report_frames(t0, 0x30) == (20, 0x30)       # stock: 48
    n, v = _report_frames(t1, _report_push(t1))
    assert (n, v) == (16, 30)                            # 48 - 18 units
    # the 0xB / 0xC pairs now stop on the same value
    assert _clamp(t1, 0x8E4EE0, 0x8E4F28, 99) == v
    assert _clamp(t1, 0x8E4D00, 0x8E4D48, 99) == v
    # "Set units." (state 0xA) keeps its own stock push
    assert struct.unpack_from('<I', t1, 0x8D9710)[0] == P.REPORT_STOCK


def test_button_draws_are_skipped_with_the_stack_balanced():
    t0 = nxmap.Main(MAIN).text
    t1, _bss = _patched()
    for bl_va, _w, add_va, _a, _n in P.BUTTON_SITES:
        assert struct.unpack_from('<I', t1, bl_va)[0] == P.NOP
        lo = bl_va - 12                     # ldr esp ; sub 4 ; str ; bl
        hi = add_va + 4

        def setup(cpu, mem):
            mem.setu(REG + 0x10, ESP, 4)
            cpu.x[22] = REG
        cpu, mem, out = _run_block(t1, lo, hi, setup)
        esp = mem.u(REG + 0x10, 4)
        # the patched block leaves esp in w8/w0, ready to store: stock would
        # have been ESP - 4 (ret slot) + 4 (callee ret) + 0xC (args)
        reg = 8 if add_va == 0x8D707C else 0
        assert cpu.x[reg] & 0xFFFFFFFF == ESP + 0xC, hex(cpu.x[reg])
        assert out == hi
    # stock keeps the calls
    for bl_va, bl_w, *_ in P.BUTTON_SITES:
        assert struct.unpack_from('<I', t0, bl_va)[0] == bl_w


def test_msg_and_buttons_can_be_restored():
    assert P.msg_y({P.MSG_ENV: '64'}) == 64
    assert not P.buttons_removed({P.BUTTONS_ENV: '1'})
    assert P.buttons_removed({})
