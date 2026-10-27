"""BUILD 565: Fort Condor at 16:9 (ff7nx_condor).

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

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import arm64emu as E  # noqa: E402
import ff7nx_condor as C  # noqa: E402
import nxmap  # noqa: E402

MAIN = os.path.join(HERE, 'dump', 'exefs', 'main')
CAVE = 0x400000
SP = 0x7F000000
REGS = 0x6000000             # host address of the emulated register file
ESP = 0x0019F000             # guest esp at the call
SRC = 0x0019FFF8             # straddles a 4 KB page on purpose
DST = 0x0019DFFC             # so does this one


def _s32(v):
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v & 0x80000000 else v


def _setup(cpu, mem, src, dst):
    mem.setu(C.REGFILE_GOT, REGS, 8)
    mem.setu(REGS + 0x10, ESP, 4)
    g = cpu.guest_to_host
    mem.setu(g(ESP), 0x5F0000, 4)               # return address
    mem.setu(g(ESP + 4), SRC, 4)
    mem.setu(g(ESP + 8), DST, 4)
    for base, vals in ((SRC, src), (DST, dst)):
        for i, v in enumerate(vals):
            mem.setu(g(base + 4 * i), v & 0xFFFFFFFF, 4)


def _read(cpu, mem, base, n):
    return [_s32(mem.u(cpu.guest_to_host(base + 4 * i), 4)) for i in range(n)]


def run_blit(my, src=(96, 0, 416, 220), dst=(0, 20, 580, 460), words=None,
             at=CAVE):
    words = words or C.build_blit(my)(at, lambda i: at + 4 * i)
    mem = E.Mem()
    cpu = E.Cpu(mem, paged=True)
    _setup(cpu, mem, src, dst)
    cpu.x[19], cpu.x[20], cpu.x[30] = 0x1919, 0x2020, 0xABCDEF
    cpu.sp = SP
    out = cpu.run(at, words)
    return out, cpu, mem


def test_blit_cave_widens_both_rects_in_place():
    out, cpu, mem = run_blit(C.MY)
    assert out == C.BLIT_BACK
    assert _read(cpu, mem, SRC, 4) == [96 - 54, -10, 416 + 54, 230]
    assert _read(cpu, mem, DST, 4) == [-108, 0, 580, 460]   # r/b not read
    # registers the caller owns, and the displaced `str d8, [sp,#-0x60]!`
    assert cpu.x[19] == 0x1919 and cpu.x[20] == 0x2020
    assert cpu.x[30] == 0xABCDEF
    assert cpu.sp == SP - 0x60


def test_blit_cave_without_uncrop_keeps_the_rows():
    out, cpu, mem = run_blit(0)
    assert out == C.BLIT_BACK
    assert _read(cpu, mem, SRC, 4) == [42, 0, 470, 220]
    assert _read(cpu, mem, DST, 4) == [-108, 20, 580, 460]


def test_widened_window_is_exactly_the_16_9_frame():
    # 427 PSX units are needed (854 game px / 2); 320 + 2*54 = 428 covers it
    assert 320 + 2 * C.MX >= 854 / 2
    assert 2 * C.MX >= 107                                  # left edge -107
    assert 220 + 2 * C.MY == 240                            # 480 game px


def _patched(wide=True, uncrop=True):
    d = tempfile.mkdtemp()
    try:
        dest = os.path.join(d, 'main')
        rep = C.apply_to_nso(MAIN, dest, stock=MAIN, wide=wide, uncrop=uncrop)
        return rep, nxmap.Main(dest).text
    finally:
        shutil.rmtree(d)


def _w(t, va):
    return struct.unpack_from('<I', t, va)[0]


def _entry(t, site):
    hook = _w(t, site)
    assert hook >> 26 == 0x05
    imm = hook & 0x3FFFFFF
    return site + 4 * (imm - (1 << 26) if imm & (1 << 25) else imm)


def _cave(t0, t1, entry):
    """The placed cave, as long as it runs: up to and including its first
    unconditional `b` out (every cave here ends that way)."""
    code = {}
    va = entry
    while True:
        w = _w(t1, va)
        code[va] = w
        if w >> 26 == 0x05:
            return code
        va += 4


def test_apply_writes_only_the_listed_words_and_the_caves():
    import ff7nx_deadspace as DS
    rep, t1 = _patched()
    t0 = nxmap.Main(MAIN).text
    lo, hi = DS.part(MAIN, 'condor')
    changed = {va for va in range(0, len(t0), 4) if t0[va:va + 4] != t1[va:va + 4]}
    words = (C.CLEAR_WORDS + C.UNIT_X_WORDS + C.UNIT_Y_WORDS
             + C.UNIT_TOP_WORDS + C.HUD_WORDS)
    listed = {va for va, _s, _n in words} | {C.BLIT_SITE, C.CAM_SITE,
                                             C.VIEWPORT_SITE, C.PROJ_SITE}
    assert listed <= changed
    assert all(va in listed or lo <= va < hi for va in changed)
    for va, _s, new in words:
        assert _w(t1, va) == new
    for va, st, _n in C.OLD_VIEWPORT_WORDS:        # the game keeps 640x440
        assert _w(t1, va) == st
    for site in (C.BLIT_SITE, C.CAM_SITE, C.VIEWPORT_SITE, C.PROJ_SITE):
        assert lo <= _entry(t1, site) < hi
    entry = _entry(t1, C.BLIT_SITE)
    out, cpu, mem = run_blit(C.MY, words=_cave(t0, t1, entry), at=entry)
    assert out == C.BLIT_BACK
    assert _read(cpu, mem, SRC, 4) == [42, -10, 470, 230]
    assert rep['wide'] and rep['uncrop']


def test_viewport_cave_calls_the_driver_full_screen_then_replays():
    _rep, t1 = _patched()
    t0 = nxmap.Main(MAIN).text
    entry = _entry(t1, C.VIEWPORT_SITE)
    calls = []

    def drv(cpu):
        calls.append(tuple(cpu.x[i] & 0xFFFFFFFF for i in range(4)))
        for r in range(0, 19):
            cpu.x[r] = E.GARBAGE
    mem = E.Mem()
    cpu = E.Cpu(mem, paged=True, native={C.DRV_SETVIEWPORT: drv})
    cpu.x[21] = REGS
    mem.setu(REGS + 0x10, 0x1111, 4)
    mem.setu(REGS + 0x14, 0x2222, 4)
    cpu.sp = SP
    out = cpu.run(entry, _cave(t0, t1, entry))
    assert out == C.VIEWPORT_BACK
    assert calls == [(0, 0, 640, 480)]
    assert cpu.x[8] & 0xFFFFFFFF == 0x1111 and cpu.x[9] & 0xFFFFFFFF == 0x2222
    assert cpu.sp == SP


def _run_camera(t0, t1, cam, cur):
    entry = _entry(t1, C.CAM_SITE)
    mem = E.Mem()
    cpu = E.Cpu(mem, paged=True)
    g = cpu.guest_to_host
    for va, v in ((C.CAM_X, cam[0]), (C.CAM_Y, cam[1]),
                  (C.CURSOR_X, cur[0]), (C.CURSOR_Y, cur[1])):
        mem.setu(g(va), v & 0xFFFF, 2)
    cpu.x[19], cpu.x[20], cpu.x[21], cpu.x[30] = 0x19, 0x20, 0x21, 0xABC
    cpu.sp = SP
    out = cpu.run(entry, _cave(t0, t1, entry))
    assert out == C.CAM_BACK
    assert (cpu.x[19], cpu.x[20], cpu.x[30]) == (0x19, 0x20, 0xABC)
    assert cpu.sp == SP - 0x30                      # replayed str x21 pre-index
    rd = lambda va: E.s16(mem.u(g(va), 2))          # noqa: E731
    return (rd(C.CAM_X), rd(C.CAM_Y)), (rd(C.CURSOR_X), rd(C.CURSOR_Y))


def test_camera_clamp_keeps_the_16_9_window_on_the_picture():
    _rep, t1 = _patched()
    t0 = nxmap.Main(MAIN).text
    cases = [((96, 0), (256, 100), (96, 10), (256, 100)),
             ((0, 0), (0, 0), (54, 10), (0, 0)),
             ((-1, -1), (-3, -2), (54, 10), (0, 0)),        # a stock wrap
             ((192, 804), (512, 1008), (138, 794), (512, 1008)),
             ((150, 900), (600, 1100), (138, 794), (512, 1008)),
             ((100, 400), (200, 500), (100, 400), (200, 500))]
    for cam, cur, want_cam, want_cur in cases:
        assert _run_camera(t0, t1, cam, cur) == (want_cam, want_cur), cam
    for lo_, hi_ in ((54, 138),):
        assert lo_ - C.MX == 0 and hi_ + 320 + C.MX == 512
    assert 10 - C.MY == 0 and 794 + 220 + C.MY == 1024


def test_without_16_9_only_the_clear_ships():
    rep, t1 = _patched(wide=False)
    t0 = nxmap.Main(MAIN).text
    changed = [va for va in range(0, len(t0), 4) if t0[va:va + 4] != t1[va:va + 4]]
    assert changed == [C.CLEAR_WORDS[0][0]]


def test_without_uncrop_the_viewport_is_stock():
    _rep, t1 = _patched(uncrop=False)
    t0 = nxmap.Main(MAIN).text
    for va, st, _n in C.UNIT_Y_WORDS:
        assert _w(t1, va) == st
    assert _w(t1, C.VIEWPORT_SITE) == C.VIEWPORT_STOCK
    # camera y keeps the stock range when the rows are not extended
    assert _run_camera(t0, t1, (0, 0), (0, 0))[0] == (54, 0)
    assert _run_camera(t0, t1, (0, 804), (0, 0))[0] == (54, 804)


def test_mixed_state_is_refused():
    t = bytearray(nxmap.Main(MAIN).text)
    va, _st, new = C.UNIT_X_WORDS[0]
    struct.pack_into('<I', t, va, new)
    try:
        C._put_words(t, C.UNIT_X_WORDS, C.UNIT_ANCHORS, 'unit x')
    except ValueError as e:
        assert 'mixed' in str(e)
    else:
        raise AssertionError('mixed state accepted')


def test_a_build_565_module_is_refused():
    import ff7nx_deadspace as DS
    t = bytearray(nxmap.Main(MAIN).text)
    for va, _st, new in C.OLD_VIEWPORT_WORDS:
        struct.pack_into('<I', t, va, new)
    lo, hi = DS.part(MAIN, 'condor')
    bump = DS.Bump(lo, hi)

    def place(build):
        before = dict(bump.placed)
        e = bump.put(build)
        return e, {k: v for k, v in bump.placed.items() if k not in before}
    try:
        C.patch_text(t, place)
    except ValueError as e:
        assert '565' in str(e)
    else:
        raise AssertionError('BUILD 565 words accepted')


def _cull(text, x, cam, camy=100, y=150):
    """Run the REAL translated cull (x86 0x601B07..0x601B65) on `text`.
    Returns True when the unit is drawn (falls through to +0x8F9AC8)."""
    lo, hi = 0x8F991C, 0x8F9AC8
    words = list(struct.unpack('<%dI' % ((hi - lo) // 4), text[lo:hi]))
    mem = E.Mem()
    cpu = E.Cpu(mem, paged=True)
    ebp, unit = 0x0019F100, 0x00D00000
    g = cpu.guest_to_host
    cpu.x[22] = REGS
    mem.setu(REGS + 0x14, ebp, 4)
    mem.setu(g(ebp - 4), unit, 4)
    mem.setu(g(unit + 0x48), x & 0xFFFF, 2)
    mem.setu(g(unit + 0x4A), y & 0xFFFF, 2)
    mem.setu(g(0xC613CC), cam, 2)
    mem.setu(g(0xC60B00), cam, 2)
    mem.setu(g(0xC613D8), camy, 2)
    cpu.x[19] = 0xC613D8
    cpu.sp = SP
    out = cpu.run(lo, words)
    assert out in (hi, 0x8FA1B0), hex(out)
    return out == hi


def test_unit_cull_stock_is_the_4_3_window():
    t0 = nxmap.Main(MAIN).text
    for cam in (0, 96, 192):
        for x in range(cam - 100, cam + 420, 7):
            assert _cull(t0, x, cam) == (cam <= x <= cam + 320), (cam, x)


def test_unit_cull_patched_is_the_16_9_window_plus_a_body():
    _rep, t1 = _patched()
    for cam in (0, 5, 96, 192):
        for x in range(cam - 100, cam + 420, 3):
            want = cam - C.UNIT_PAD <= x <= cam + 320 + C.UNIT_PAD
            assert _cull(t1, x, cam) == want, (cam, x)
    # and the bottom grows with the uncrop: camy .. camy + 260
    assert _cull(t1, 96, 96, camy=100, y=360)
    assert not _cull(t1, 96, 96, camy=100, y=361)


def test_viewport_rewrite_is_the_identity_matrix():
    import ws_emu
    r = ws_emu.run(0, 0, 640, 480, 1280, 720, 640, 480, 13)
    assert (r['x1'], r['y1'], r['x2'], r['y2']) == (0, 0, 1280, 720)
    assert r['_22'] == 1.0 and r['_42'] == 0.0
    r = ws_emu.run(0, 20, 640, 440, 1280, 720, 640, 480, 13)
    assert r['_22'] < 0.92                   # the stock squash this removes


def _run_projection(t1, mode):
    entry = _entry(t1, C.PROJ_SITE)
    mem = E.Mem()
    cpu = E.Cpu(mem, paged=True)
    mem.setu(C.MODE_SLOT, 0x5550000, 8)
    mem.setu(0x5550000, mode, 4)
    cpu.x[8] = 0x1234
    cpu.sp = SP
    out = cpu.run(entry, _cave(nxmap.Main(MAIN).text, t1, entry))
    assert out == C.PROJ_BACK and cpu.sp == SP
    return cpu.x[8] & 0xFFFFFFFF


def test_projection_22_scaled_in_condor_only():
    _rep, t1 = _patched()
    assert _w(t1, C.PROJ_SITE) >> 26 == 0x05
    f = lambda b: struct.unpack('<f', struct.pack('<I', b))[0]   # noqa: E731
    assert _run_projection(t1, 13) == C.PROJ_22
    assert abs(f(C.PROJ_22) + 440.0 / 480.0) < 1e-6
    for mode in (0, 1, 2, 3, 9, 10, 11, 12, 14):
        assert _run_projection(t1, mode) == 0xBF800000, mode
    # the rows it buys: 720 x 440/480 = 660, the map's own 440 x 1.5
    assert round(720 * 440 / 480) == 440 * 1.5


def test_projection_cave_absent_without_uncrop():
    _rep, t1 = _patched(uncrop=False)
    assert _w(t1, C.PROJ_SITE) == C.PROJ_STOCK


# ------------------------------------------------------------ BUILD 662
def test_unit_cull_top_is_opened_by_the_pad():
    """The top test was y >= camy with no margin; units vanished on screen
    near the top edge. Patched: camy - UNIT_TOP_PAD <= y <= camy + 260."""
    t0 = nxmap.Main(MAIN).text
    _rep, t1 = _patched()
    for camy in (10, 100, 794):
        for y in range(camy - 80, camy + 300, 3):
            assert _cull(t0, 96, 96, camy=camy, y=y) == \
                (camy <= y <= camy + 240), (camy, y)
            assert _cull(t1, 96, 96, camy=camy, y=y) == \
                (camy - C.UNIT_TOP_PAD <= y <= camy + 260), (camy, y)
    # the x window is unchanged by it
    assert not _cull(t1, 96 - C.UNIT_PAD - 1, 96, camy=100, y=90)
    assert _cull(t1, 96 - C.UNIT_PAD, 96, camy=100, y=90)


def test_gauges_move_to_the_16_9_edges():
    """SPEED 24 units right, SIDE/ENEMY 34 units left: every x the HUD
    routine builds for them, and nothing else in it."""
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    t0 = nxmap.Main(MAIN).text
    _rep, t1 = _patched()
    m = nxmap.Main(MAIN)
    lo, hi = m.extent(0x5F88F3)
    changed = [va for va in range(lo, hi, 4) if t0[va:va + 4] != t1[va:va + 4]]
    assert changed == sorted(va for va, _s, _n in C.HUD_WORDS)
    for va, _s, _n in C.HUD_WORDS[:-2]:
        old = next(md.disasm(t0[va:va + 4], va))
        new = next(md.disasm(t1[va:va + 4], va))
        imm_old = int(old.op_str.split('#')[1], 16)
        imm_new = int(new.op_str.split('#')[1], 16)
        assert old.mnemonic == 'add' and new.mnemonic == 'sub'
        assert imm_old - (-imm_new) == C.HUD_LEFT, (hex(va), imm_old, imm_new)
    for va, _s, _n in C.HUD_WORDS[-2:]:
        o = int(next(md.disasm(t0[va:va + 4], va)).op_str.split('#')[1], 16)
        n = int(next(md.disasm(t1[va:va + 4], va)).op_str.split('#')[1], 16)
        assert n - o == C.HUD_RIGHT
    # measured margins (720p, 3 px a unit): both ~96 px after the move
    assert abs((169 - 3 * C.HUD_RIGHT) - 96) <= 2
    assert abs((198 - 3 * C.HUD_LEFT) - 96) <= 2


def test_gauges_can_be_left_stock():
    assert not C.hud_enabled({C.HUD_ENV: '0'})
    assert C.hud_enabled({})
