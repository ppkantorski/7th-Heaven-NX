"""BUILD 580: FFNx's 60 FPS partial-animation frame mapping (CANM!/CANIM).

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
import ff7nx_canim60 as C  # noqa: E402
import nxmap  # noqa: E402

MAIN = os.path.join(HERE, 'dump', 'exefs', 'main')
SP = 0x7F000000
CAVE = 0x5000000
SCRIPT = 0x1200000        # guest script base
EVENTS = 0x1300000
ANIMS = 0x1400000
OBJ = 0x1500000


def _run(first, last, speed, divide, model=3, ent=7, frames=180,
         anim_table=True):
    hook = 0x96301C
    words = C.build_cave(hook, divide)(CAVE, lambda i: CAVE + 4 * i)
    code = {CAVE + 4 * i: w for i, w in enumerate(words)}
    mem = E.Mem()
    cpu = E.Cpu(mem, paged=True)
    g = cpu.guest_to_host
    pos = 0x40
    mem.setu(g(C.G_ENT), ent, 1)
    mem.setu(g(C.G_MODEL + ent), model, 1)
    mem.setu(g(C.G_POS + ent * 2), pos, 2)
    mem.setu(g(C.G_SCRIPT), SCRIPT, 4)
    for i, v in enumerate((0xB1, 0x00, first, last, speed)):
        mem.setu(g(SCRIPT + pos + i), v, 1)
    mem.setu(g(C.G_EVENT), EVENTS, 4)
    ev = EVENTS + (model & 0xFF) * 0x88
    mem.setu(g(ev + 0x68), 0x1111, 2)
    mem.setu(g(ev + 0x6A), 0x2222, 2)
    mem.setu(g(C.G_ANIM), ANIMS if anim_table else 0, 4)
    mem.setu(g(ANIMS + model * 0x190 + 0x178), OBJ, 4)
    mem.setu(g(OBJ + 8), frames, 4)
    cpu.x[19] = 0xCC0964
    cpu.sp = SP
    out = cpu.run(CAVE, code)
    assert out == hook + 4, hex(out)
    assert cpu.x[0] & 0xFFFFFFFF == 0xCC0964 - 0xD4    # displaced replayed
    assert cpu.x[19] == 0xCC0964 and cpu.sp == SP
    return mem.u(g(ev + 0x68), 2), mem.u(g(ev + 0x6A), 2)


def test_wcrimb_rope_single_frame_steps_cover_the_doubled_file():
    # CANM! 00 f f 01 for f = 0..0x59 now walks 0..179, two frames a call
    for f in (0, 1, 0x31, 0x59):
        cur, last = _run(f, f, 1, False)
        assert (cur, last) == (16 * 2 * f, 2 * f + 1)
    assert _run(0x59, 0x59, 1, False)[1] == 179          # the far end


def test_ranges_speed_and_clamp():
    assert _run(0x14, 0x27, 1, False) == (16 * 0x28, 0x4F)
    assert _run(0x14, 0x27, 2, False) == (16 * 0x28, 0x4F // 2)
    assert _run(0x14, 0x27, 2, True) == (16 * 0x28 // 2, 0x4F // 2)
    assert _run(0, 0xFF, 1, False, frames=100)[1] == 99   # clamped
    assert _run(0, 0xFF, 1, False, anim_table=False)[1] == 0x1FF


def test_no_model_or_zero_speed_writes_nothing():
    assert _run(5, 6, 1, False, model=0xFF) == (0x1111, 0x2222)
    assert _run(5, 6, 0, False) == (0x1111, 0x2222)


def test_installs_on_the_real_module_and_touches_nothing_else():
    if not os.path.exists(MAIN):
        return
    import ff7nx_deadspace as DS
    d = tempfile.mkdtemp()
    try:
        dest = os.path.join(d, 'main')
        info = C.apply_to_nso(MAIN, dest, stock=MAIN)
        t0 = nxmap.Main(MAIN).text
        t1 = nxmap.Main(dest).text
    finally:
        shutil.rmtree(d)
    lo, hi = DS.part(MAIN, 'canim')
    ch = [va for va in range(0, len(t0), 4) if t0[va:va + 4] != t1[va:va + 4]]
    hooks = {h for _n, h, _s, _d in C.SITES}
    assert all(va in hooks or lo <= va < hi for va in ch)
    assert hooks <= set(ch) and info['words'] > 2
    for _n, hook, _s, _d in C.SITES:
        w = struct.unpack_from('<I', t1, hook)[0]
        assert w >> 26 == 0x05                          # b, not bl
