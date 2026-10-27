"""BUILD 561: coaster analogue aim (ff7nx_coaster) and the widened world
(ff7nx_coasterworld).

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
import arm64emu as E  # noqa: E402
import ff7nx_analog as AN  # noqa: E402
import ff7nx_coaster as C  # noqa: E402
import nxmap  # noqa: E402

MAIN = os.path.join(HERE, 'dump', 'exefs', 'main')
CAVE = 0x400000
SP = 0x7F000000
OBJ = 0x5000000


def _f(v):
    return struct.unpack('<I', struct.pack('<f', v))[0]


def run_aim(stick, pos=(160, 120), frac=(0, 0), speed=8.0):
    words = C.build_aim(speed)(CAVE, lambda i: CAVE + 4 * i)
    mem = E.Mem()
    cpu = E.Cpu(mem, paged=True)
    # input object chain: GOT -> p0 ->[0] p1 ->[8] p2 ->[0] p3 ->[0x88] obj
    chain = [0x5100000, 0x5200000, 0x5300000, 0x5400000, OBJ]
    mem.setu(AN.INPUT_GOT, chain[0], 8)
    offs = (0,) + tuple(AN.INPUT_CHAIN)
    for i, off in enumerate(AN.INPUT_CHAIN):
        mem.setu(chain[i] + off, chain[i + 1], 8)
    del offs
    up, down, right, left = stick
    for off, v in ((AN.OBJ_UP, up), (AN.OBJ_DOWN, down),
                   (AN.OBJ_RIGHT, right), (AN.OBJ_LEFT, left)):
        mem.setu(OBJ + off, _f(v), 4)
    host = cpu.guest_to_host(0xC3FB58)
    mem.setu(host, pos[0] & 0xFFFF, 2)
    mem.setu(host + 2, frac[0], 2)
    mem.setu(host + 4, pos[1] & 0xFFFF, 2)
    mem.setu(host + 6, frac[1], 2)
    cpu.x[21] = 0xC3FB58
    cpu.x[25] = 0x6000000
    mem.setu(0x6000000 + 0x10, 0x1234, 4)
    cpu.x[19], cpu.x[20], cpu.x[30] = 0x1919, 0x2020, 0xABCDEF
    cpu.sp = SP
    out = cpu.run(CAVE, words)
    assert cpu.sp == SP and cpu.x[19] == 0x1919 and cpu.x[20] == 0x2020
    assert cpu.x[30] == 0xABCDEF
    x = E.s16(mem.u(host, 2)) + mem.u(host + 2, 2) / 65536.0
    y = E.s16(mem.u(host + 4, 2)) + mem.u(host + 6, 2) / 65536.0
    return out, x, y, cpu


def test_idle_inside_dead_zone_runs_stock():
    out, x, y, cpu = run_aim((0.1, 0.0, 0.05, 0.0))
    assert out == C.AIM_BACK and (x, y) == (160, 120)
    assert cpu.x[8] & 0xFFFFFFFF == 0x1234          # replayed ldr w8


def test_full_tilt_is_speed_any_angle():
    for deg in range(0, 360, 15):
        r = math.radians(deg)
        sx, sy = math.cos(r), math.sin(r)             # sy > 0 = stick up
        stick = (max(sy, 0), max(-sy, 0), max(sx, 0), max(-sx, 0))
        out, x, y, _ = run_aim(stick)
        assert out == C.AIM_SKIP
        dx, dy = x - 160, y - 120
        assert abs(math.hypot(dx, dy) - 8.0) < 0.01, (deg, dx, dy)
        # screen y grows downward
        assert abs(math.atan2(-dy, dx) - math.atan2(sy, sx)) < 1e-3 or \
            abs(abs(math.atan2(-dy, dx) - math.atan2(sy, sx)) - 2 * math.pi) \
            < 1e-3, deg


def test_fraction_accumulates():
    # a light push: 0.3 right -> t = (0.3-0.15)/0.85, step = 8 t^2
    t = (0.3 - 0.15) / 0.85
    step = 8 * t * t
    x, frac = 100, 0
    total = 0.0
    for _ in range(20):
        _, nx, _, _ = run_aim((0, 0, 0.3, 0), pos=(x, 50), frac=(frac, 0))
        total += step
        x, frac = int(math.floor(nx)), int(round((nx - math.floor(nx)) * 65536))
    assert abs((x + frac / 65536.0) - (100 + total)) < 1e-3
    assert x == 100 + int(total)                   # moved, sub-unit steps kept


def test_negative_positions_floor_correctly():
    _, x, y, _ = run_aim((0, 0, 0, 1.0), pos=(3, 0), frac=(0, 0))
    assert abs(x - (3 - 8)) < 1e-3                 # -5: the game clamps it


def _coaster_lgp():
    import lgp
    a = lgp.Archive(os.path.join(HERE, 'dump', 'romfs', 'ff7', 'workingdir',
                                 'data', 'minigame', 'coaster.lgp'))
    return (a.index['xbin.bin']['payload'], a.index['xbinadr.bin']['payload'])


def _play(xbin, adr, pattern, W):
    """Drive the four streams exactly as x86 0x5EDC59 / 0x5EDD82 do,
    including the first frame's extra triangle step; count list faults."""
    t = W._table(adr)
    st = {k: W.read_stream(xbin, *W._region(t, len(xbin), k))[:2141]
          for k in (8, 9, 2, 3)}
    p = dict.fromkeys(st, 0)
    first, faults, T = 1, 0, 0
    live = {8: set(), 2: set()}
    for f in range(100000):
        n = pattern(f)
        T += n
        if T >= 2140:
            break
        for k, cnt in ((8, n + first), (2, n)):
            for _ in range(cnt):
                for i in st[k][p[k]]:
                    faults += i in live[k]
                    live[k].add(i)
                p[k] += 1
        for k, lk, cnt in ((9, 8, n + first), (3, 2, n)):
            for _ in range(cnt):
                for i in st[k][p[k]]:
                    faults += i not in live[lk]
                    live[lk].discard(i)
                p[k] += 1
        first = 0
    return faults


def test_world_widening_keeps_the_lists_consistent():
    import random
    import ff7nx_coasterworld as W
    xbin, adr = _coaster_lgp()
    assert W.rewrite(xbin, adr, 0, 0) == xbin
    pats = (lambda f: 1, lambda f: int(f % 26 == 0),
            lambda f, r=random.Random(3): int(r.random() < 0.1))
    for lr in ((1, 2), (0, 3), (2, 3)):
        new = W.rewrite(xbin, adr, *lr)
        assert len(new) == len(xbin)
        for pat in pats:
            assert _play(new, adr, pat, W) == 0, lr
    assert W.peaks(xbin, adr, 2141) == (1157, 210)
    assert W.peaks(W.rewrite(xbin, adr, 1, 2), adr, 2141) == (1478, 246)


def test_world_widening_is_a_superset_in_time():
    import ff7nx_coasterworld as W
    xbin, adr = _coaster_lgp()
    new = W.rewrite(xbin, adr, 1, 2)
    t = W._table(adr)
    for ka, kr in W.STREAMS:
        def live_sets(x):
            a = W.read_stream(x, *W._region(t, len(x), ka))[:2141]
            r = W.read_stream(x, *W._region(t, len(x), kr))[:2141]
            cur, out = set(), []
            for s in range(2141):
                cur |= set(a[s])
                cur -= set(r[s])
                out.append(frozenset(cur))
            return out
        old, wide = live_sets(xbin), live_sets(new)
        for s in range(2141):
            assert old[s] <= wide[s], (ka, s)
            # everything drawn now was drawn by stock within [s-2, s+1]
            near = frozenset().union(*old[max(0, s - 2):s + 2])
            assert wide[s] <= near, (ka, s)


def test_stock_anchors_and_install():
    t = nxmap.Main(MAIN).text
    assert C.check_text(t) == []
    import ff7nx_deadspace as DS
    lo, hi = DS.part(MAIN, 'coaster', MAIN)
    # 1 KB from BUILD 687 (the stale 0x800 here predates it); 512 B since
    # BUILD 742 gave the upper half to ff7nx_sfxbattle's tables
    assert hi - lo == 0x200 and lo == DS.part(MAIN, 'sub')[1]
    assert hi == DS.part(MAIN, 'sfxtab')[0]
    d = tempfile.mkdtemp()
    try:
        dest = os.path.join(d, 'main')
        rep = C.apply_to_nso(MAIN, dest, stock=MAIN, speed=8.0)
        assert rep['words'] > 70
        # it still fits its smaller part with room to spare
        assert 4 * rep['words'] <= (hi - lo) - 128, rep['words']
        t2 = nxmap.Main(dest).text
        w = struct.unpack_from('<I', t2, C.AIM_SITE)[0]
        assert (w >> 26) == 0x05
        # the dead region is still dead (liveness proof holds on our output)
        DS.region(dest)
        # a second install is refused rather than stacked
        try:
            C.apply_to_nso(dest, os.path.join(d, 'again'), stock=MAIN)
        except ValueError:
            pass
        else:
            raise AssertionError('second install accepted')
    finally:
        shutil.rmtree(d)


# ---- BUILD 563: track yaw ------------------------------------------------
def _rot_rows():
    import ff7nx_coasterworld as W
    xbin, adr = _coaster_lgp()
    t = W._table(adr)
    off, end = W._region(t, len(xbin), 7)
    return [struct.unpack_from('<4h', xbin, off + 8 * i)
            for i in range((end - off) // 8)]


def _wrap(d):
    while d > 0x800:
        d -= 0x1000
    while d < -0x800:
        d += 0x1000
    return d


def _yaw(rows, pos, fixed):
    """x86 0x5EA194's yaw, stock or with the three BUILD 563 words."""
    i, f = pos >> 16, pos & 0xFFFF
    cur, nxt = rows[i][1], rows[i + 1][1]
    if fixed:
        d = _wrap(cur - nxt)
    else:
        d = nxt - cur
        d = -((-d) >> 5) if d < 0 else d >> 5          # idiv-style /32
        d = _wrap(d)
    return -cur + ((f * d) >> 16)


def test_yaw_is_continuous_and_smooth():
    rows = _rot_rows()
    n = len(rows)
    for speed in (0x2710, 0x8000, 0x1D4C0):
        worst = {False: 0, True: 0}
        for fixed in (False, True):
            pos = 0
            prev = _yaw(rows, pos, fixed)
            while ((pos + speed) >> 16) < n - 2:
                pos += speed
                y = _yaw(rows, pos, fixed)
                worst[fixed] = max(worst[fixed], abs(_wrap(y - prev)))
                prev = y
        assert worst[True] < worst[False]
        # never more than the real node-to-node turn within one tick
        max_node = max(abs(_wrap(rows[i + 1][1] - rows[i][1]))
                       for i in range(n - 1))
        steps = max(1, -(-speed // 0x10000))
        assert worst[True] <= max_node * steps + 1, (speed, worst)
    # continuity at every node boundary
    for i in range(n - 2):
        end_prev = _yaw(rows, (i << 16) | 0xFFFF, True)
        start = _yaw(rows, (i + 1) << 16, True)
        assert abs(_wrap(end_prev - start)) <= 1, i


def test_yaw_words():
    t = nxmap.Main(MAIN).text
    assert C.yaw_state(t) == 'stock'
    buf = bytearray(t)
    w = C.patch_yaw(buf)
    assert len(w) == 3 and C.yaw_state(bytes(buf)) == 'applied'
    assert C.patch_yaw(buf) == {}                       # idempotent
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    got = [next(md.disasm(buf[va:va + 4], va)) for va, _s, _n in C.YAW_WORDS]
    assert [(i.mnemonic, i.op_str) for i in got] == [
        ('sub', 'w8, w8, w9'), ('nop', ''), ('mov', 'w20, w8')]
    # w8 is next written (not read) right after: the stack-slot reload
    assert struct.unpack_from('<I', buf, 0x89E7E8)[0] == 0xB94016A8


# ---- BUILD 564: cursor edges, draw distance --------------------------------
def test_far_widening_keeps_the_lists_consistent():
    import random
    import ff7nx_coasterworld as W
    xbin, adr = _coaster_lgp()
    lf = W.far_steps(1.25)
    assert lf == 26
    new = W.rewrite(xbin, adr, 1, 2, lf)
    assert len(new) == len(xbin)
    for pat in (lambda f: 1, lambda f, r=random.Random(7): int(r.random() < .1)):
        assert _play(new, adr, pat, W) == 0
    assert W.peaks(new, adr, 2141) == (1801, 316)
    # a superset of the 561 set, and never more than LF steps early
    t = W._table(adr)
    base = W.rewrite(xbin, adr, 1, 2)
    for ka, kr in W.STREAMS:
        def live(x):
            a = W.read_stream(x, *W._region(t, len(x), ka))[:2141]
            r = W.read_stream(x, *W._region(t, len(x), kr))[:2141]
            cur, out = set(), []
            for s in range(2141):
                cur |= set(a[s])
                cur -= set(r[s])
                out.append(frozenset(cur))
            return out
        b, w, o = live(base), live(new), live(xbin)
        for s in range(2141):
            assert b[s] <= w[s]
            assert w[s] <= frozenset().union(*o[max(0, s - 2):s + lf + 1])


def test_far_and_edge_words():
    t = nxmap.Main(MAIN).text
    buf = bytearray(t)
    w = C._put_words(buf, C.EDGE_WORDS, C.EDGE_ANCHORS, 'edge')
    assert len(w) == 10
    assert C._put_words(buf, C.EDGE_WORDS, C.EDGE_ANCHORS, 'edge') == {}
    f = C.far_words(1.25)
    assert f[0][1] == 0x528E0008 and f[1][1] == 0x72A8CBE8   # 14300.0f
    far = struct.unpack('<f', struct.pack(
        '<I', (((f[1][2] >> 5) & 0xFFFF) << 16) | ((f[0][2] >> 5) & 0xFFFF)))[0]
    assert far == 17875.0
    assert (f[2][2] >> 5) & 0xFFFF == 13013 and (f[3][2] >> 5) & 0xFFFF == 17875
    assert len(C._put_words(buf, f, C.FAR_ANCHORS, 'far')) == 4
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    dis = {va: next(md.disasm(buf[va:va + 4], va)) for va, _s, _n in C.EDGE_WORDS}
    assert (dis[0x887A70].mnemonic, dis[0x887A70].op_str) == ('cmp', 'w8, #0x175')
    assert dis[0x887A74].op_str == '#0x887a8c' and dis[0x887A74].mnemonic == 'b.le'
    assert (dis[0x887A9C].mnemonic, dis[0x887A9C].op_str) == ('cmn', 'w8, #0x35')
    assert dis[0x887AA8].mnemonic == 'b.ge' and dis[0x887AA8].op_str == '#0x887ab8'
    assert dis[0x887AA4].op_str == 'w11, #-0x35'


def _clamp_model(x):
    """What the rewritten words do (right then left)."""
    if not x <= 373:
        x = 373
    if not x + 53 >= 0:
        x = -53
    return x


def test_clamp_model():
    for x in (-400, -54, -53, 0, 160, 320, 373, 374, 900):
        assert _clamp_model(x) == max(-53, min(373, x))
