"""BUILD 670: scripted field camera moves in sub-unit steps (ff7nx_subcam).
Every cave runs in unicorn against a fake translator / guest; the module is
laid onto the dump when it is present.

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
import struct
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import ff7nx_subcam as SC                                     # noqa: E402

unicorn = pytest.importorskip('unicorn')
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE   # noqa: E402
from unicorn.arm64_const import *                              # noqa: E402,F403

STATE = 0x3FEC330
CAVE = 0x99F350
G = 0x2_0000_0000              # host address of guest 0
CTX = 0x1_8000_0000
STACK = 0x1_9000_0000
DUMP = os.path.join(HERE, 'dump', 'exefs', 'main')


class Rig:
    def __init__(self):
        uc = self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        uc.mem_map(0x900000, 0x300000)                 # caves, interpolators, sites
        uc.mem_map(0x10D0000, 0x40000)                 # draw path, translator
        uc.mem_map(0x12CE000, 0x1000)                  # GOT: ctx, mode slot
        uc.mem_map(STATE & ~0xFFF, 0x4000)
        uc.mem_map(G + 0xCC0000, 0x10000)
        uc.mem_map(G + 0xCFF000, 0x1000)
        uc.mem_map(G + 0x0FF00000, 0x10000)            # guest stack
        uc.mem_map(CTX, 0x1000)
        uc.mem_map(STACK - 0x10000, 0x11000)
        uc.mem_map(0x7000000, 0x1000)                  # mode
        uc.mem_write(SC.CTX_PTR, struct.pack('<Q', CTX))
        uc.mem_write(SC.MODE_SLOT, struct.pack('<Q', 0x7000000))
        self.mode(2)
        self.calls = []
        uc.hook_add(UC_HOOK_CODE, self._tr, begin=SC.TRANSLATE, end=SC.TRANSLATE)
        uc.hook_add(UC_HOOK_CODE, self._interp, begin=SC.LIN_ARM, end=SC.LIN_ARM)
        uc.hook_add(UC_HOOK_CODE, self._interp, begin=SC.SMOOTH_ARM, end=SC.SMOOTH_ARM)

    def mode(self, m):
        self.uc.mem_write(0x7000000, struct.pack('<I', m))

    def _tr(self, uc, addr, size, ud):
        uc.reg_write(UC_ARM64_REG_X0, G + (uc.reg_read(UC_ARM64_REG_X0) & 0xFFFFFFFF))
        for r in range(1, 19):                         # the translator's contract
            uc.reg_write(UC_ARM64_REG_X0 + r if r < 29 else 0, 0xDEAD0000 + r)
        uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_X30))

    def _interp(self, uc, addr, size, ud):
        esp = struct.unpack('<I', uc.mem_read(CTX + 0x10, 4))[0]
        a, b, n, s = struct.unpack('<4i', uc.mem_read(G + esp + 4, 16))
        if addr == SC.LIN_ARM:
            v = a + int((b - a) * s / n)
        else:                                          # the byte-angle table
            ang = ((s << 12) // n >> 5) + 0x80
            v = a + (int((math.cos(ang / 256 * 2 * math.pi) * 4096) + 4096) * (b - a) >> 13)
        self.calls.append((addr, a, b, n, s, v))
        uc.mem_write(CTX, struct.pack('<i', v))
        uc.mem_write(CTX + 0x10, struct.pack('<I', esp + 4))      # pops its slot
        for r in range(0, 19):
            uc.reg_write(UC_ARM64_REG_X0 + r, 0xBEEF0000 + r)
        uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_X30))

    def put(self, at, words):
        self.uc.mem_write(at, b''.join(struct.pack('<I', w) for w in words))

    def run(self, start, stop, sp=STACK - 0x200):
        self.uc.reg_write(UC_ARM64_REG_SP, sp)
        self.uc.emu_start(start, stop, count=200000)

    def st(self, off, fmt='<I'):
        return struct.unpack(fmt, self.uc.mem_read(STATE + off, struct.calcsize(fmt)))

    def set(self, off, fmt, *v):
        self.uc.mem_write(STATE + off, struct.pack(fmt, *v))

    def g16(self, ga, v):
        self.uc.mem_write(G + ga, struct.pack('<h', v))


def _capture(r, axis, smooth, args):
    common = CAVE
    r.put(common, SC.capture_common(common, STATE))
    stub = CAVE + 0x400
    r.put(stub, SC.capture_stub(stub, axis, smooth, common))
    esp = 0x0FF00100
    r.uc.mem_write(CTX + 0x10, struct.pack('<I', esp))
    r.uc.mem_write(G + esp + 4, struct.pack('<4i', *args))
    site = 0x9D8000
    r.uc.mem_write(site, struct.pack('<I', 0x94000000 | (((stub - site) >> 2) & 0x3FFFFFF)))
    for k in (19, 20, 21, 22):
        r.uc.reg_write(UC_ARM64_REG_X19 + k - 19, 0x5150 + k)
    r.run(site, site + 4)
    assert [r.uc.reg_read(UC_ARM64_REG_X19 + k) for k in range(4)] == [0x5150 + k for k in (19, 20, 21, 22)]
    assert r.uc.reg_read(UC_ARM64_REG_SP) == STACK - 0x200
    return r.calls[-1]


@pytest.mark.parametrize('smooth', (0, 1))
@pytest.mark.parametrize('step', (0, 1, 37, 100, 163, 399, 400))
def test_capture_keeps_the_float_move_and_calls_the_real_one(smooth, step):
    r = Rig()
    a, b, n = 104, 5, 400
    call = _capture(r, 1, smooth, (a, b, n, step))
    assert call[0] == (SC.SMOOTH_ARM if smooth else SC.LIN_ARM) and call[1:5] == (a, b, n, step)
    t = step / n
    want = a + (b - a) * (t if not smooth else (1 - math.cos(math.pi * t)) / 2)
    assert r.st(SC.S_VALID + 4) == (1,)
    assert abs(r.st(SC.S_F + 4, '<f')[0] - want) < 2e-3
    assert r.st(SC.S_I + 4, '<i') == (call[5],)
    assert r.st(SC.S_VALID) == (0,)                     # the other axis untouched



def _on(r):
    at = CAVE + 0x200
    w = SC.on_cave(at, STATE)
    r.put(at, w)
    r.put(SC.ON_SITE, [0xD503201F])                       # where it returns
    for k in range(19):
        r.uc.reg_write(UC_ARM64_REG_X0 + k, 0x7700 + k)
    r.uc.reg_write(UC_ARM64_REG_X30, 0x123456)
    sp = STACK - 0x200
    r.run(at, SC.ON_SITE + 4, sp)
    assert [r.uc.reg_read(UC_ARM64_REG_X0 + k) for k in range(19)] == [0x7700 + k for k in range(19)]
    assert r.uc.reg_read(UC_ARM64_REG_X30) == 0x123456
    assert r.uc.reg_read(UC_ARM64_REG_SP) == sp


def _prime(r, F, I, valid=(1, 1), delta=None, mult=2):
    r.set(SC.S_VALID, '<2I', *valid)
    r.set(SC.S_F, '<2f', *F)
    r.set(SC.S_I, '<2i', *I)
    r.set(SC.S_TSET, '<I', 1)
    r.set(SC.S_RING, '<4I', 9, 9, 9, 9)
    d = delta or I
    r.g16(SC.DELTA_X, d[0])
    r.g16(SC.DELTA_Y, d[1])
    r.uc.mem_write(G + SC.MULT, struct.pack('<i', mult))


def test_on_turns_the_kept_move_into_a_shift():
    r = Rig()
    _prime(r, (95.872, 128.94), (97, 130))
    _on(r)
    sx, sy = r.st(SC.S_SHIFT, '<2f')
    # snapped to tenths of a unit (FFNx MIN_STEP), then * mult
    assert abs(sx - round((95.872 - 97) * 10) / 10 * 2) < 1e-5
    assert abs(sy - round((128.94 - 130) * 10) / 10 * 2) < 1e-5
    assert r.st(SC.S_Q, '<2i') == (int(sx * 65536), int(sy * 65536))
    assert r.st(SC.S_FLAG) == (1,) and r.st(SC.S_ACTIVE) == (1,)
    assert r.st(SC.S_VALID, '<2I') == (0, 0) and r.st(SC.S_TSET) == (0,)
    assert r.st(SC.S_HEAD) == (0,) and r.st(SC.S_RING, '<4I') == (0, 0, 0, 0)


def test_on_drops_a_clamped_axis_and_a_big_shift():
    r = Rig()
    _prime(r, (95.872, 128.94), (97, 130), delta=(90, 130))
    _on(r)
    assert r.st(SC.S_SHIFT, '<f')[0] == 0.0 and r.st(SC.S_SHIFT + 4, '<f')[0] != 0.0
    r = Rig()
    _prime(r, (85.0, 130.0), (97, 130))
    _on(r)
    assert r.st(SC.S_SHIFT, '<2f') == (0.0, 0.0) and r.st(SC.S_FLAG) == (0,)


def test_on_snaps_to_tenths():
    for f, want in ((96.97, -0.0), (96.94, -0.1), (97.04, 0.0), (97.06, 0.1), (96.5, -0.5)):
        r = Rig()
        _prime(r, (f, 130.0), (97, 130))
        _on(r)
        assert abs(r.st(SC.S_SHIFT, '<f')[0] - want * 2) < 1e-5, f


def test_on_with_no_move_is_still():
    r = Rig()
    _prime(r, (95.5, 128.5), (97, 130), valid=(0, 0))
    r.set(SC.S_SHIFT, '<2f', 1.0, 1.0)
    r.set(SC.S_DEF, '<I', 1)
    _on(r)
    assert r.st(SC.S_FLAG) == (0,) and r.st(SC.S_SHIFT, '<2f') == (0.0, 0.0)
    assert r.st(SC.S_ACTIVE) == (0,) and r.st(SC.S_DEF) == (0,)
    assert r.st(SC.S_RING, '<I') == (9,)                 # untouched when still


def _draw(r, M, flag=1, dflag=0, tset=0, T=(0.0, 0.0), shift=(-2.256, -2.12)):
    at = CAVE + 0x600
    r.put(at, SC.draw_cave(at, STATE))
    r.put(SC.DRAW_SITE + 4, [0xD503201F])
    r.set(SC.S_FLAG, '<I', flag)
    r.set(SC.S_DEF, '<I', dflag)
    r.set(SC.S_TSET, '<I', tset)
    r.set(SC.S_T, '<2f', *T)
    r.set(SC.S_SHIFT, '<2f', *shift)
    sp = STACK - 0x400
    r.uc.mem_write(sp + SC.MATRIX_SP, struct.pack('<16f', *np.asarray(M, np.float32).flatten(order='F')))
    for k in range(3):
        r.uc.reg_write(UC_ARM64_REG_X0 + k, 0x2000 + k)
    r.run(at, SC.DRAW_SITE + 4, sp)
    assert [r.uc.reg_read(UC_ARM64_REG_X0 + k) for k in range(3)] == [0x2000, 0x2001, 0x2002]
    assert r.uc.reg_read(UC_ARM64_REG_X3) == sp + 0x14
    return np.array(struct.unpack('<16f', r.uc.mem_read(sp + SC.MATRIX_SP, 64))).reshape(4, 4, order='F')


ORTHO = np.array([[2 / 640, 0, 0, -1], [0, -2 / 480, 0, 1], [0, 0, 1, 0], [0, 0, 0, 1]], np.float32)
PERSP = np.array([[1.2, 0, 0.1, 0], [0, 1.6, 0.2, 0], [0, 0, 1.001, -0.1], [0, 0, 1, 0]], np.float32)


def test_draw_moves_flat_and_3d_draws_by_the_same_pixels():
    r = Rig()
    M = _draw(r, ORTHO)
    tx, ty = r.st(SC.S_T, '<2f')
    assert abs(tx - ORTHO[0, 0] * -2.256) < 1e-6 and abs(ty - ORTHO[1, 1] * -2.12) < 1e-6
    assert np.allclose(M - ORTHO, [[0, 0, 0, tx], [0, 0, 0, ty], [0, 0, 0, 0], [0, 0, 0, 0]], atol=1e-6)
    P = _draw(r, PERSP, tset=1, T=(tx, ty))
    for v in ([3, 4, 10, 1], [-50, 20, 3, 1]):
        c0, c1 = PERSP @ v, P @ v
        assert np.allclose(c1[:2] / c1[3] - c0[:2] / c0[3], (tx, ty), atol=1e-5)


def test_draw_leaves_everything_else_alone():
    r = Rig()
    assert np.allclose(_draw(r, ORTHO, flag=0), ORTHO)
    r.mode(1)
    assert np.allclose(_draw(r, ORTHO), ORTHO)
    r.mode(2)
    assert np.allclose(_draw(r, PERSP, flag=0, dflag=1, tset=1, T=(0.01, 0.02))[:, 2],
                       PERSP[:, 2] + [0.01, 0.02, 0, 0])


def test_deferred_groups_of_the_scene_are_noted_and_found():
    r = Rig()
    at = CAVE + 0x900
    r.put(at, SC.alloc_cave(at, STATE))
    r.put(SC.ALLOC_SITE + 4, [0xD503201F])
    entry = STATE + 0x1000
    for flag, head in ((1, 517), (0, 3)):
        r.set(SC.S_FLAG, '<I', flag)
        r.set(SC.S_HEAD, '<I', head)
        r.uc.reg_write(UC_ARM64_REG_X20, entry)
        r.run(at, SC.ALLOC_SITE + 4)
    assert r.st(SC.S_HEAD) == (3,) and r.st(SC.S_RING + 4 * 5) == (entry & 0xFFFFFFFF,)
    d = CAVE + 0xA00
    r.put(d, SC.defer_cave(d, STATE))
    r.put(SC.DEFER_SITE + 4, [0xD503201F])
    r.uc.mem_write(entry + 0x50, struct.pack('<I', 0xCAFE))
    r.uc.reg_write(UC_ARM64_REG_X20, entry)
    for active, x21, found in ((1, entry, 1), (1, entry + 0x40, 0), (0, entry, 0)):
        r.set(SC.S_ACTIVE, '<I', active)
        r.set(SC.S_DEF, '<I', 1)
        r.set(SC.S_CUR, '<I', 3)
        r.uc.reg_write(UC_ARM64_REG_X21, x21)
        r.run(d, SC.DEFER_SITE + 4)
        assert r.st(SC.S_DEF) == (found,)
        assert r.uc.reg_read(UC_ARM64_REG_X0) & 0xFFFFFFFF == 0xCAFE


def test_off_ends_the_scene():
    r = Rig()
    at = CAVE + 0xB00
    r.put(at, SC.off_cave(at, STATE))
    r.put(SC.OFF_SITE + 4, [0xD503201F])
    r.set(SC.S_FLAG, '<I', 1)
    r.set(SC.S_DEF, '<I', 1)                    # the in-scene flush's last entry
    r.uc.reg_write(UC_ARM64_REG_X8, 12)
    r.run(at, SC.OFF_SITE + 4)
    assert r.st(SC.S_FLAG) == (0,) and r.uc.reg_read(UC_ARM64_REG_X8) == 8
    assert r.st(SC.S_DEF) == (0,)              # BUILD 707: windows / timer stay put


def test_707_entries_queued_after_the_scene_never_match():
    """The in-scene flush frees the deferred list; the countdown timer's
    entry, queued after the scene, gets a freed scene address. Its alloc
    takes that address out of the ring, so draw_deferred does not shift it."""
    r = Rig()
    at = CAVE + 0x900
    r.put(at, SC.alloc_cave(at, STATE))
    r.put(SC.ALLOC_SITE + 4, [0xD503201F])
    entry = STATE + 0x1000
    other = STATE + 0x1100
    r.set(SC.S_ACTIVE, '<I', 1)
    r.set(SC.S_HEAD, '<I', 0)
    for e_ in (entry, other):                  # two scene entries
        r.set(SC.S_FLAG, '<I', 1)
        r.uc.reg_write(UC_ARM64_REG_X20, e_)
        r.run(at, SC.ALLOC_SITE + 4)
    r.set(SC.S_FLAG, '<I', 0)                   # after OFF: the timer reuses `entry`
    r.uc.reg_write(UC_ARM64_REG_X20, entry)
    for k in (9, 10, 11, 12):
        r.uc.reg_write(UC_ARM64_REG_X9 + k - 9, 0x7700 + k)
    r.run(at, SC.ALLOC_SITE + 4)
    assert [r.uc.reg_read(UC_ARM64_REG_X9 + k - 9) for k in (9, 10, 11, 12)] == \
        [0x7700 + k for k in (9, 10, 11, 12)]
    assert r.st(SC.S_HEAD) == (2,)
    assert r.st(SC.S_RING, '<2I') == (0, other & 0xFFFFFFFF)
    d = CAVE + 0xA00
    r.put(d, SC.defer_cave(d, STATE))
    r.put(SC.DEFER_SITE + 4, [0xD503201F])
    r.uc.mem_write(entry + 0x50, struct.pack('<I', 0xCAFE))
    r.uc.reg_write(UC_ARM64_REG_X20, entry)
    for x21, found in ((entry, 0), (other, 1)):
        r.set(SC.S_DEF, '<I', 1)
        r.uc.reg_write(UC_ARM64_REG_X21, x21)
        r.run(d, SC.DEFER_SITE + 4)
        assert r.st(SC.S_DEF) == (found,)


def test_pick_takes_the_parallax_share_back():
    import ff7nx_subparallax as P
    for cam in (None, STATE):
        w = P.pick_body(lambda i: 0x9F0000 + 4 * i, 0x3FECEA8, 0, 0x20, cam)
        assert w[-1] == 0xD65F03C0                          # straight-line, ret
        if cam is None:
            assert w == P.pick_body(lambda i: 0x9F0000 + 4 * i, 0x3FECEA8, 0, 0x20)


@pytest.mark.skipif(not os.path.exists(DUMP), reason='no dump')
def test_module_lays_out_on_the_dump(tmp_path):
    import nxmap
    assert SC.check_sites(nxmap.Main(DUMP).img) == []
    rep = SC.apply_to_nso(DUMP, str(tmp_path / 'main'), DUMP)
    img = nxmap.Main(str(tmp_path / 'main')).img
    assert SC.state_of(img) == rep['state']
    import ff7nx_deadspace as DS
    lo, hi = DS.part(DUMP, 'subcam')
    assert rep['bytes'] <= hi - lo
    for site, _a, _s in SC.CAPTURE:
        w = struct.unpack_from('<I', img, site)[0]
        assert site + 4 * (((w & 0x3FFFFFF) ^ 0x2000000) - 0x2000000) == rep['stubs'][site]


def test_switch():
    assert SC.enabled({}) and not SC.enabled({SC.ENV: '0'})
