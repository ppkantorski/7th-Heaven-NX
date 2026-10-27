"""BUILD 693: the player camera in sub-unit steps (ff7nx_subfollow).

The follow cave runs in unicorn against a fake translator / guest, then
ff7nx_subcam's ON cave turns what it kept into the frame's shift. The range
rectangle the cave clamps the float to is checked against the game's own
clip (0x6438F6), run from the recompiled module. The module is laid onto a
built exefs/main (subcam + pointers installed) when one is present.

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
import random
import struct
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import ff7nx_subfollow as F                                   # noqa: E402
import ff7nx_subcam as SC                                     # noqa: E402

unicorn = pytest.importorskip('unicorn')
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE   # noqa: E402
from unicorn.arm64_const import *                              # noqa: E402,F403

STATE = 0x3FF2310
BSS = 0x3FF1CA8
CAVE = 0x997D50
G = 0x2_0000_0000              # host address of guest 0
CTX = 0x1_8000_0000
STACK = 0x1_9000_0000
HDR = 0x00E00000               # guest: the trigger header
EBP = 0x0FF00200
ESP = 0x0FF00100
STOCK = os.path.join(HERE, 'dump', 'exefs', 'main')
BUILT = os.path.join(HERE, 'sdout', 'atmosphere', 'contents',
                     '0100A5B00BDC6000', 'exefs', 'main')
for _p in (os.environ.get('SEVENTH_NX_TEST_MAIN_STOCK'),):
    if _p:
        STOCK = _p
for _p in (os.environ.get('SEVENTH_NX_TEST_MAIN_BUILT'),):
    if _p:
        BUILT = _p


def s16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def f32(v):
    return struct.unpack('<f', struct.pack('<f', v))[0]


class Rig:
    def __init__(self):
        uc = self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        uc.mem_map(0x990000, 0x20000)                  # caves, the site
        uc.mem_map(0x9E0000, 0x20000)                  # subcam ON site
        uc.mem_map(0x10F0000, 0x10000)                 # translator
        uc.mem_map(0x12CE000, 0x1000)                  # GOT: ctx
        uc.mem_map(BSS & ~0xFFF, 0x2000)
        uc.mem_map(G + 0xCC0000, 0x10000)
        uc.mem_map(G + 0xCFF000, 0x1000)
        uc.mem_map(G + HDR, 0x1000)
        uc.mem_map(G + 0x0FF00000, 0x10000)            # guest stack
        uc.mem_map(CTX, 0x1000)
        uc.mem_map(STACK - 0x10000, 0x11000)
        uc.mem_write(SC.CTX_PTR, struct.pack('<Q', CTX))
        uc.mem_write(CTX + 0x10, struct.pack('<II', ESP, EBP))
        uc.mem_write(G + F.HEADER_PTR, struct.pack('<I', HDR))
        uc.hook_add(UC_HOOK_CODE, self._tr, begin=F.TRANSLATE, end=F.TRANSLATE)
        self.at = CAVE
        self.uc.mem_write(self.at, b''.join(
            struct.pack('<I', w) for w in F.follow_cave(self.at, STATE, BSS)))
        self.uc.mem_write(F.SITE + 4, struct.pack('<I', 0xD503201F))

    def _tr(self, uc, addr, size, ud):
        uc.reg_write(UC_ARM64_REG_X0, G + (uc.reg_read(UC_ARM64_REG_X0) & 0xFFFFFFFF))
        for r in range(1, 19):                         # the translator's contract
            uc.reg_write(UC_ARM64_REG_X0 + r, 0xDEAD0000 + r)
        for r in range(0, 8):                          # it leaves v-regs alone,
            pass                                       # the cave relies on none
        uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_X30))

    def field(self, L, T, R, B, rail=0, view=(0, 0)):
        self.uc.mem_write(G + HDR + 0xC, struct.pack('<4h', L, T, R, B))
        self.uc.mem_write(G + HDR + 0x14, bytes([rail]))
        self.uc.mem_write(G + F.VIEW_X, struct.pack('<i', view[0]))
        self.uc.mem_write(G + F.VIEW_Y, struct.pack('<i', view[1]))

    def frame(self, screen, point):
        self.uc.mem_write(BSS + 8, struct.pack('<2f', *screen))
        self.uc.mem_write(G + EBP - F.POINT, struct.pack('<2h', *point))
        self.uc.mem_write(STATE, bytes(0x40))
        for k in range(19, 29):
            self.uc.reg_write(UC_ARM64_REG_X19 + k - 19, 0x5150 + k)
        self.uc.reg_write(UC_ARM64_REG_X21, CTX)
        sp = STACK - 0x200
        self.uc.reg_write(UC_ARM64_REG_SP, sp)
        self.uc.emu_start(self.at, F.SITE + 4, count=100000)
        regs = [self.uc.reg_read(UC_ARM64_REG_X19 + k) for k in range(10)]
        want = [0x5150 + k for k in range(19, 29)]
        want[2] = CTX
        assert regs == want
        assert self.uc.reg_read(UC_ARM64_REG_SP) == sp
        # the displaced ldp: w8 = esp, w9 = ebp
        assert self.uc.reg_read(UC_ARM64_REG_X8) & 0xFFFFFFFF == ESP
        assert self.uc.reg_read(UC_ARM64_REG_X9) & 0xFFFFFFFF == EBP
        valid = struct.unpack('<2I', self.uc.mem_read(STATE + SC.S_VALID, 8))
        fl = struct.unpack('<2f', self.uc.mem_read(STATE + SC.S_F, 8))
        iv = struct.unpack('<2i', self.uc.mem_read(STATE + SC.S_I, 8))
        return [(valid[a], fl[a], iv[a]) for a in (0, 1)]


def model(screen, point, rng, view, rail):
    """What the cave must keep, per axis: None or (float delta, int delta)."""
    L, T, R, B = rng
    out = []
    for a, (lo, hi) in enumerate(((L + 160, R - 160), (T + 120, B - 120))):
        sc = f32(screen[a])
        if rail in (1, 2) or math.isnan(sc):
            out.append(None)
            continue
        t = int(sc)                                    # toward zero
        if s16(t - view[a]) != point[a]:
            out.append(None)
            continue
        f = f32(point[a] + f32(sc - t))
        if f > hi:
            f = hi
        if f < lo:
            f = lo
        if not abs(f - point[a]) < 1.0:
            out.append(None)
            continue
        out.append((-f, s16(-point[a])))
    return out


def clip_int(p, rng):
    L, T, R, B = rng
    x, y = p
    if x > R - 160:
        x = R - 160
    if x < L + 160:
        x = L + 160
    if y > B - 120:
        y = B - 120
    if y < T + 120:
        y = T + 120
    return x, y


def _check(r, screen, rng, view=(0, 0), rail=0):
    r.field(*rng, rail=rail, view=view)
    pre = tuple(s16(int(f32(s)) - v) if not math.isnan(s) else 0
                for s, v in zip(screen, view))
    point = clip_int(pre, rng)
    got = r.frame(screen, point)
    want = model(screen, point, rng, view, rail)
    for a in (0, 1):
        if want[a] is None:
            assert got[a][0] == 0, (screen, rng, a, got)
        else:
            assert got[a][0] == 1, (screen, rng, a, got)
            assert abs(got[a][1] - want[a][0]) < 1e-4 and got[a][2] == want[a][1]
    return got, want


def test_inside_the_range_keeps_the_remainder():
    r = Rig()
    got, _ = _check(r, (412.375, 201.75), (-600, -400, 600, 400), view=(160, 120))
    assert got[0][2] == -(412 - 160) and abs(got[0][1] - -(252.375)) < 1e-4
    assert got[1][2] == -(201 - 120) and abs(got[1][1] - -(81.75)) < 1e-4


def test_negative_positions_truncate_toward_zero():
    r = Rig()
    got, _ = _check(r, (-37.25, -0.5), (-400, -300, 400, 300))
    assert got[0][2] == 37 and abs(got[0][1] - 37.25) < 1e-4
    assert got[1][2] == 0 and abs(got[1][1] - 0.5) < 1e-4


def test_at_the_range_edge_the_float_never_passes_it():
    r = Rig()
    rng = (-400, -300, 400, 300)                       # x in [-240, 240]
    for sx, want in ((240.6, 0), (240.0, 0), (239.4, 1), (-240.7, 0), (-239.2, 1)):
        got, _ = _check(r, (sx, 10.5), rng)
        moved = got[0][0] and -got[0][1] != -got[0][2]
        assert moved == bool(want), (sx, got)      # at the edge: no shift
        assert not got[0][0] or abs(got[0][1]) <= 240
    got, _ = _check(r, (300.5, 10.5), rng)            # clipped: x not kept
    assert got[0][0] == 0 and got[1][0] == 1


def test_narrow_field_and_rail_and_nan_keep_nothing():
    r = Rig()
    got, _ = _check(r, (10.5, 10.5), (-100, -100, 100, 100))   # smaller than the screen
    assert got[0][0] == 0 and got[1][0] == 0
    got, _ = _check(r, (10.5, 10.5), (-400, -300, 400, 300), rail=1)
    assert got[0][0] == 0 and got[1][0] == 0
    got, _ = _check(r, (10.5, 10.5), (-400, -300, 400, 300), rail=2)
    assert got[0][0] == 0 and got[1][0] == 0
    _check(r, (float('nan'), 10.5), (-400, -300, 400, 300))


def test_random():
    rnd = random.Random(693)
    r = Rig()
    for _ in range(400):
        L, T = rnd.randint(-1500, 0), rnd.randint(-1200, 0)
        rng = (L, T, L + rnd.randint(200, 2400), T + rnd.randint(150, 2000))
        view = (rnd.choice((0, 160, -96)), rnd.choice((0, 120, 30)))
        screen = tuple(rnd.uniform(-1800, 1800) for _ in range(2))
        if rnd.random() < 0.2:                         # on a range edge
            screen = (rng[2] - 160 + view[0] + rnd.uniform(-1.5, 1.5), screen[1])
        _check(r, screen, rng, view, rail=rnd.choice((0, 0, 0, 3)))


def test_subcam_turns_it_into_the_frames_shift():
    """follow -> the game stores delta = -point -> subcam's ON cave."""
    r = Rig()
    uc = r.uc
    on = 0x991000
    uc.mem_write(on, b''.join(struct.pack('<I', w) for w in SC.on_cave(on, STATE)))
    uc.mem_write(SC.ON_SITE + 4, struct.pack('<I', 0xD503201F))
    uc.mem_write(G + SC.MULT, struct.pack('<i', 3))
    for sx, sy in ((100.37, 50.81), (100.04, -50.96), (-7.5, 3.0)):
        r.field(-400, -300, 400, 300)
        p = (int(f32(sx)), int(f32(sy)))
        r.frame((sx, sy), p)
        uc.mem_write(G + SC.DELTA_X, struct.pack('<2h', s16(-p[0]), 0))
        uc.mem_write(G + SC.DELTA_Y, struct.pack('<h', s16(-p[1])))
        uc.reg_write(UC_ARM64_REG_SP, STACK - 0x200)
        uc.emu_start(on, SC.ON_SITE + 4, count=100000)
        shift = struct.unpack('<2f', uc.mem_read(STATE + SC.S_SHIFT, 8))
        for a, s in enumerate((sx, sy)):
            rem = f32(s) - int(f32(s))
            want = round(-rem * 10) / 10 * 3
            assert abs(shift[a] - want) < 1e-4, (s, shift)


# ------------------------------------------------- the game's own clip
def _module(path):
    import nxmap
    return nxmap.Main(path)


@pytest.mark.skipif(not os.path.exists(STOCK), reason='no dump')
def test_the_rectangle_is_the_games_clip():
    """0x6438F6 (+0xA11530), recompiled, on random points and ranges: it is
    exactly clip_int, the rectangle the cave clamps the float to."""
    m = _module(STOCK)
    img = bytes(m.img)
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    size = (len(img) + 0xFFF) & ~0xFFF
    uc.mem_map(0, size)
    uc.mem_write(0, img)
    uc.mem_map(G + 0x00E00000, 0x1000)
    uc.mem_map(G + 0x0CF0000, 0x10000)
    uc.mem_map(G + 0x0FF00000, 0x10000)
    uc.mem_map(CTX, 0x1000)
    uc.mem_map(STACK - 0x10000, 0x11000)
    uc.mem_write(SC.CTX_PTR, struct.pack('<Q', CTX))
    uc.mem_write(G + F.HEADER_PTR, struct.pack('<I', HDR))

    def tr(uc, addr, size, ud):
        uc.reg_write(UC_ARM64_REG_X0, G + (uc.reg_read(UC_ARM64_REG_X0) & 0xFFFFFFFF))
        uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_X30))

    def ret(uc, addr, size, ud):                       # the return-slot check
        uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_X30))
    uc.hook_add(UC_HOOK_CODE, tr, begin=F.TRANSLATE, end=F.TRANSLATE)
    uc.hook_add(UC_HOOK_CODE, ret, begin=0xA550, end=0xA550)
    rnd = random.Random(6438)
    stop = 0x990000
    for _ in range(300):
        L, T = rnd.randint(-1500, 300), rnd.randint(-1200, 300)
        rng = (L, T, L + rnd.randint(0, 2400), T + rnd.randint(0, 2000))
        p = (rnd.randint(-2000, 2000), rnd.randint(-2000, 2000))
        uc.mem_write(G + HDR + 0xC, struct.pack('<4h', *rng))
        pt = 0x0FF00400
        uc.mem_write(G + pt, struct.pack('<2h', *p))
        uc.mem_write(CTX, bytes(0x30))
        uc.mem_write(CTX + 0x10, struct.pack('<II', ESP, EBP))
        uc.mem_write(G + ESP + 4, struct.pack('<I', pt))
        uc.reg_write(UC_ARM64_REG_X30, stop)
        uc.reg_write(UC_ARM64_REG_SP, STACK - 0x200)
        uc.emu_start(0xA11530, stop, count=200000)
        got = struct.unpack('<2h', uc.mem_read(G + pt, 4))
        assert got == clip_int(p, rng), (p, rng, got)


@pytest.mark.skipif(not os.path.exists(BUILT), reason='no built exefs/main')
def test_module_lays_out_on_a_built_main(tmp_path):
    import nxmap
    img = nxmap.Main(BUILT).img
    if F.check_sites(img):
        pytest.skip('built main already has it, or lacks subcam / pointers: %s'
                    % F.check_sites(img))
    rep = F.apply_to_nso(BUILT, str(tmp_path / 'main'), None,
                         region=(CAVE, CAVE + 0x400))
    out = nxmap.Main(str(tmp_path / 'main')).img
    w = struct.unpack_from('<I', out, F.SITE)[0]
    assert F.SITE + 4 * (((w & 0x3FFFFFF) ^ 0x2000000) - 0x2000000) == rep['cave']
    words = F.follow_cave(rep['cave'], rep['state'], rep['bss'])
    assert bytes(out[rep['cave']:rep['cave'] + 4 * len(words)]) == \
        b''.join(struct.pack('<I', x) for x in words)
    assert rep['bytes'] <= 0x400
    assert rep['state'] == SC.state_of(img) and rep['bss'] == F.pointers_bss(img)


def test_switch():
    assert F.enabled({}) and not F.enabled({F.ENV: '0'})



# ---------------------------------------------------------------- BUILD 772
class RailRig(Rig):
    """the same rig with the BUILD 772 rail path built in"""
    def __init__(self):
        super().__init__()
        self.uc.mem_write(self.at, b''.join(
            struct.pack('<I', w) for w in F.follow_cave(self.at, STATE, BSS, rail=True)))

    def rail_frame(self, screen, point, sreg):
        self.uc.reg_write(UC_ARM64_REG_S0, struct.unpack('<I', struct.pack('<f', sreg[0]))[0])
        self.uc.reg_write(UC_ARM64_REG_S1, struct.unpack('<I', struct.pack('<f', sreg[1]))[0])
        return self.frame(screen, point)


def _rnd_away(v):
    return int(math.floor(abs(v) + 0.5)) * (1 if v >= 0 else -1)


def test_772_rail_keeps_the_float_point_on_the_rail():
    r = RailRig()
    rng = (-346, -208, 346, 208)                       # mds5_3's range
    r.field(*rng, rail=1)
    for fx, fy in ((-12.375, 7.6), (100.49, -40.5), (-0.4, 0.4), (150.25, 60.75)):
        point = (_rnd_away(f32(fx)), _rnd_away(f32(fy)))
        got = r.rail_frame((0.0, 0.0), point, (fx, fy))
        for a, f in enumerate((fx, fy)):
            assert got[a][0] == 1, (fx, fy, got)
            assert abs(got[a][1] - -f32(f)) < 1e-4
            assert got[a][2] == s16(-point[a])


def test_772_rail_floats_that_do_not_round_to_the_point_are_ignored():
    r = RailRig()
    r.field(-346, -208, 346, 208, rail=2)
    got = r.rail_frame((0.0, 0.0), (10, 20), (12.2, 20.1))
    assert got[0][0] == 0 and got[1][0] == 1           # x off by two: dropped
    got = r.rail_frame((0.0, 0.0), (10, 20), (float('nan'), 19.6))
    assert got[0][0] == 0 and got[1][0] == 1


def test_772_rail_float_is_clamped_to_the_range():
    r = RailRig()
    rng = (-346, -208, 346, 208)                       # x in [-186, 186]
    r.field(*rng, rail=1)
    got = r.rail_frame((0.0, 0.0), (186, 0), (186.4, 0.25))
    assert got[0][0] == 1 and abs(got[0][1] - -186.0) < 1e-6
    assert got[1][0] == 1 and abs(got[1][1] - -0.25) < 1e-6


def test_772_non_rail_fields_are_unchanged_by_the_rail_path():
    """with the rail path built in, a normal field gives what 693 gave,
    whatever junk s0 / s1 hold"""
    r, rr = Rig(), RailRig()
    rnd = random.Random(772)
    for _ in range(200):
        rng = (rnd.randint(-900, -300), rnd.randint(-700, -200),
               rnd.randint(300, 900), rnd.randint(200, 700))
        screen = (rnd.uniform(-500, 500), rnd.uniform(-400, 400))
        view = (rnd.randint(-50, 50), rnd.randint(-50, 50))
        r.field(*rng, rail=0, view=view)
        rr.field(*rng, rail=0, view=view)
        pre = tuple(s16(int(f32(sc)) - v) for sc, v in zip(screen, view))
        point = clip_int(pre, rng)
        a = r.frame(screen, point)
        b = rr.rail_frame(screen, point, (rnd.uniform(-999, 999), rnd.uniform(-999, 999)))
        assert a == b


def test_772_the_cave_fits_its_part():
    words = F.follow_cave(0, STATE, BSS, rail=True)
    import ff7nx_deadspace as DS
    assert 4 * len(words) + 16 <= DS.WINREP_AT - DS.FOLLOW_AT



# ---------------------------------------------------------------- BUILD 773
class TwoRig(Rig):
    """follow_cave2: both entries in one image"""
    def __init__(self, rail=True):
        super().__init__()
        self.words, self.entry2 = F.follow_cave2(self.at, STATE, BSS, rail=rail)
        self.uc.mem_write(self.at, b''.join(struct.pack('<I', w) for w in self.words))
        self.uc.mem_write(F.SCR_SITE + 4, struct.pack('<I', 0xD503201F))

    def walk(self, screen, point, sreg=(0.0, 0.0)):
        self.uc.reg_write(UC_ARM64_REG_S0, struct.unpack('<I', struct.pack('<f', sreg[0]))[0])
        self.uc.reg_write(UC_ARM64_REG_S1, struct.unpack('<I', struct.pack('<f', sreg[1]))[0])
        return self.frame(screen, point)

    def scripted(self, screen, point):
        uc = self.uc
        uc.mem_write(BSS + 8, struct.pack('<2f', *screen))
        uc.mem_write(G + EBP - F.SCR_POINT, struct.pack('<2h', *point))
        uc.mem_write(STATE, bytes(0x40))
        for k in range(19, 29):
            uc.reg_write(UC_ARM64_REG_X19 + k - 19, 0x5150 + k)
        uc.reg_write(UC_ARM64_REG_X22, CTX)                # the context here
        uc.reg_write(UC_ARM64_REG_X21, 0x7777)
        sp = STACK - 0x200
        uc.reg_write(UC_ARM64_REG_SP, sp)
        uc.emu_start(self.entry2, F.SCR_SITE + 4, count=100000)
        regs = [uc.reg_read(UC_ARM64_REG_X19 + k) for k in range(10)]
        want = [0x5150 + k for k in range(19, 29)]
        want[2] = 0x7777
        want[3] = CTX
        assert regs == want
        assert uc.reg_read(UC_ARM64_REG_SP) == sp
        assert uc.reg_read(UC_ARM64_REG_X8) & 0xFFFFFFFF == ESP   # its ldp
        assert uc.reg_read(UC_ARM64_REG_X9) & 0xFFFFFFFF == EBP
        valid = struct.unpack('<2I', uc.mem_read(STATE + SC.S_VALID, 8))
        fl = struct.unpack('<2f', uc.mem_read(STATE + SC.S_F, 8))
        iv = struct.unpack('<2i', uc.mem_read(STATE + SC.S_I, 8))
        return [(valid[a], fl[a], iv[a]) for a in (0, 1)]


def test_773_walking_entry_is_772():
    one, two = RailRig(), TwoRig()
    rnd = random.Random(7731)
    for _ in range(300):
        rail = rnd.choice((0, 0, 1, 2))
        rng = (rnd.randint(-900, -300), rnd.randint(-700, -200),
               rnd.randint(300, 900), rnd.randint(200, 700))
        view = (rnd.randint(-40, 40), rnd.randint(-40, 40))
        screen = (rnd.uniform(-500, 500), rnd.uniform(-400, 400))
        one.field(*rng, rail=rail, view=view)
        two.field(*rng, rail=rail, view=view)
        pre = tuple(s16(int(f32(sc)) - v) for sc, v in zip(screen, view))
        point = clip_int(pre, rng)
        sreg = (point[0] + rnd.uniform(-0.6, 0.6), point[1] + rnd.uniform(-0.6, 0.6))
        assert one.rail_frame(screen, point, sreg) == two.walk(screen, point, sreg)


def test_773_scripted_follow_keeps_the_float():
    r = TwoRig()
    rnd = random.Random(7732)
    for _ in range(300):
        rng = (rnd.randint(-900, -300), rnd.randint(-700, -200),
               rnd.randint(300, 900), rnd.randint(200, 700))
        view = (rnd.randint(-40, 40), rnd.randint(-40, 40))
        rail = rnd.choice((0, 1, 2))                   # never a rail here
        r.field(*rng, rail=rail, view=view)
        screen = (rnd.uniform(-500, 500), rnd.uniform(-400, 400))
        pre = tuple(s16(int(f32(sc)) - v) for sc, v in zip(screen, view))
        point = clip_int(pre, rng)
        got = r.scripted(screen, point)
        want = model(screen, point, rng, view, 0)
        for a in (0, 1):
            if want[a] is None:
                assert got[a][0] == 0
            else:
                assert got[a][0] == 1
                assert abs(got[a][1] - want[a][0]) < 1e-4 and got[a][2] == want[a][1]


def test_773_scripted_example_mrkt2_like():
    r = TwoRig()
    r.field(-480, -360, 480, 360, view=(0, 0))
    got = r.scripted((37.625, -12.25), (37, -12))
    assert got[0] == (1, pytest.approx(-37.625), -37)
    assert got[1] == (1, pytest.approx(12.25), 12)


def test_773_fits_and_sites():
    import ff7nx_deadspace as DS
    words, e2 = F.follow_cave2(0, STATE, BSS)
    assert 4 * len(words) + 16 <= DS.WINREP_AT - DS.FOLLOW_AT
    assert 0 < e2 < 4 * len(words)
