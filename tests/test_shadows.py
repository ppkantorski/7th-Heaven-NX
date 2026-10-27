"""BUILD 645: battle model shadows -- native/shadows.c run in unicorn.

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
import struct
import sys

import numpy as np
import pytest

HERE = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, HERE)

import ff7nx_shadows as SH   # noqa: E402

unicorn = pytest.importorskip('unicorn')
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM          # noqa: E402
from unicorn.arm64_const import (UC_ARM64_REG_X0, UC_ARM64_REG_X1,   # noqa: E402
                                 UC_ARM64_REG_X2, UC_ARM64_REG_X3, UC_ARM64_REG_X4,
                                 UC_ARM64_REG_LR,
                                 UC_ARM64_REG_SP, UC_ARM64_REG_CPACR_EL1)

CODE, CTX, RS, RB, STK, RET = 0x10000, 0x40000, 0x50000, 0x60000, 0x80000, 0x9000
# BUILD 645m: a fake GL device: DEV -> W -> OBJ -> VT, VT+0x110 = FN, which
# logs (obj, sfail, zfail, zpass, mask) at LOG and counts calls at LOG+0x40
DEV, W, OBJ, VT, FN, LOG = 0xA000, 0xA100, 0xA200, 0xA300, 0xA800, 0xAC00


class Machine:
    def __init__(self):
        self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        for base in (0, CODE, CTX, RS, RB, STK - 0x8000):
            try:
                self.uc.mem_map(base, 0x10000)
            except Exception:
                pass
        self.uc.reg_write(UC_ARM64_REG_CPACR_EL1, 0x300000)   # FP on
        self.uc.mem_write(CODE, open(SH.BLOB, 'rb').read())
        self.uc.mem_write(RET, struct.pack('<I', 0xD503201F))
        self.ent = SH.entries()
        q = lambda a, v: self.uc.mem_write(a, struct.pack('<Q', v))   # noqa: E731
        q(DEV, W); q(W, OBJ); q(OBJ, VT); q(VT + 0x110, FN)
        # stp x0,x1,[LOG]; stp x2,x3,[LOG+16]; str x4,[LOG+32]; count++; ret
        import a64 as A
        code = [A.movz(9, LOG), A.stp64_off(0, 1, 9, 0), A.stp64_off(2, 3, 9, 16),
                A.str64(4, 9, 32), A.ldr(10, 9, 0x40), A.add_imm(10, 10, 1) if hasattr(A, 'add_imm') else 0x1100054A,
                A.str_(10, 9, 0x40), A.ret()]
        self.uc.mem_write(FN, b''.join(struct.pack('<I', x) for x in code))

    def call(self, name, *args):
        if name in ('sh_begin', 'sh_end') and len(args) == 3:
            args = args + (DEV,)
        if name == 'sh_begin' and len(args) == 4:
            args = args + (0,)                                 # slow path (645q)
        if name == 'sh_pre' and len(args) == 2:
            args = args + (300,)                               # index count (645p)
        regs = (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3,
                UC_ARM64_REG_X4)
        for r, v in zip(regs, args):
            self.uc.reg_write(r, v)
        self.uc.reg_write(UC_ARM64_REG_SP, STK)
        self.uc.reg_write(UC_ARM64_REG_LR, RET)
        self.uc.emu_start(CODE + self.ent[name], RET)
        return self.uc.reg_read(UC_ARM64_REG_X0) & 0xFFFFFFFF

    def mat(self, addr, m=None):
        if m is not None:
            self.uc.mem_write(addr, np.asarray(m, '<f4').tobytes())
        return np.frombuffer(bytes(self.uc.mem_read(addr, 64)), '<f4').reshape(4, 4)

    def u32(self, addr, v=None):
        if v is not None:
            self.uc.mem_write(addr, struct.pack('<I', v))
        return struct.unpack('<I', bytes(self.uc.mem_read(addr, 4)))[0]


def rot(ax, ang):
    c, s = np.cos(ang), np.sin(ang)
    m = np.eye(4)
    i, j = [(1, 2), (0, 2), (0, 1)][ax]
    m[i, i], m[i, j], m[j, i], m[j, j] = c, s, -s, c
    return m


def test_blob_kept_and_skipped_actor_flattened_then_restored():
    m = Machine()
    # the floor under the actor: a disc of radius 300, turned, at (50, 900, 4000)
    blob = np.diag([300.0, 1.0, 300.0, 1.0]) @ rot(0, 0.3) @ rot(1, 0.7)
    blob[3, :3] = (50.0, 900.0, 4000.0)
    m.mat(RS + 0x28, blob)
    m.u32(CTX, SH.MODE_BLOB)
    assert m.call('sh_pre', CTX, RS) == 1                     # skip the blob
    assert m.u32(CTX + 4) == 1                                # have_blob
    # a part of the actor, above the floor
    part = rot(1, 1.1) @ rot(0, 0.3)
    part[3, :3] = (80.0, 400.0, 3900.0)
    m.mat(RS + 0x28, part)
    m.u32(RS + 0x0C, 0)
    rb0 = bytes(range(0x38))
    m.uc.mem_write(RB + 0x740, rb0)
    m.u32(CTX, SH.MODE_ACTOR)
    assert m.call('sh_pre', CTX, RS) == 2
    m.call('sh_begin', CTX, RS, RB)
    flat = m.mat(RS + 0x28)
    assert m.u32(RS + 0x0C) == 5
    assert (m.u32(RB + 0x76C), m.u32(RB + 0x770), m.u32(RB + 0x774)) == (8, 0, 7)  # DST_COLOR, ADD, 1-DA (645o)
    assert m.u32(RB + 0x748) == 1                              # LESS
    assert m.u32(RB + 0x764) == 0                              # 667: no culling
    # every vertex of the part lands on the floor plane (lifted a hair)
    n = blob[1, :3] / np.linalg.norm(blob[1, :3])
    d = blob[3, :3] @ n
    pts = np.random.default_rng(1).uniform(-200, 200, (50, 3))
    pts = np.c_[pts, np.ones(50)]
    out = pts @ flat.astype(np.float64)
    h = out[:, :3] @ n - d
    assert np.ptp(h) < 1e-2 * 300                            # flat
    assert abs(h.mean()) < 1e-2 * 300                        # no view: no bias
    m.call('sh_end', CTX, RS, RB)
    assert np.allclose(m.mat(RS + 0x28), part)
    assert bytes(m.uc.mem_read(RB + 0x740, 0x38)) == rb0
    assert m.u32(RS + 0x0C) == 0


def test_no_blob_no_shadow():
    m = Machine()
    m.u32(CTX, SH.MODE_ACTOR)
    assert m.call('sh_pre', CTX, RS) == 0
    m.u32(CTX, 0)
    assert m.call('sh_pre', CTX, RS) == 0


def test_shader_line(tmp_path):
    src = os.path.join(HERE, 'shaders', 'wide_screen', 'lmain_vv.glsl')
    p = tmp_path / 'lmain_vv.glsl'
    p.write_text(open(src).read())
    assert SH.patch_shader(str(p))
    t = p.read_text()
    assert t.count('blendMode.x == 5') == 1
    # after the plain UV copy, so the shadow's shifted UV is the one that stays
    assert t.index('vTextureCoord = TextureCoord;') < t.index('blendMode.x == 5')
    assert t.index('blendMode.z == 0x44594E') < t.index('blendMode.x == 5')
    assert not SH.patch_shader(str(p))


def test_shader_line_replaces_the_645_line(tmp_path):
    src = os.path.join(HERE, 'shaders', 'wide_screen', 'lmain_vv.glsl')
    old = open(src).read().replace(SH.SHADER_ANCHOR,
                                   SH.OLD_SHADER_LINE + SH.SHADER_ANCHOR, 1)
    p = tmp_path / 'lmain_vv.glsl'
    p.write_text(old)
    assert SH.patch_shader(str(p))
    t = p.read_text()
    assert t.count('blendMode.x == 5') == 1 and SH.SHADER_MARK in t
    assert 'ff7nx_shadows 645' not in t


def test_fragment_branch(tmp_path):
    src = os.path.join(HERE, 'dump', 'romfs', 'ff7', 'shaders', 'colortex_p.glsl')
    if not os.path.isfile(src):
        src = os.path.join(HERE, '..', '..', '7th_heaven_nx', 'dump', 'romfs',
                           'ff7', 'shaders', 'colortex_p.glsl')
    if not os.path.isfile(src):
        pytest.skip('needs the dump')
    p = tmp_path / 'colortex_p.glsl'
    p.write_text(open(src).read())
    assert SH.patch_fragment(str(p))
    t = p.read_text()
    assert t.index(SH.FP_ANCHOR) < t.index('vTextureCoord.x < -') < t.index('discard;\n\t\n\tpColor')
    assert not SH.patch_fragment(str(p))


def test_apply_stock_main(tmp_path):
    stock = os.path.join(HERE, 'dump', 'exefs', 'main')
    if not os.path.isfile(stock):
        pytest.skip('needs the dump')
    import ff7nx_audio_cave as AC
    import a64 as A
    out = tmp_path / 'main'
    r = SH.apply_to_nso(stock, str(out), stock)
    _s, raw = AC.segments(open(out, 'rb').read())
    t = raw[0]
    for s in SH.HELPER_CALLS:
        assert struct.unpack_from('<I', t, s)[0] == A.bl(s, r['stubs']['helper'])
    assert struct.unpack_from('<I', t, SH.BLOB_CALL)[0] == A.bl(SH.BLOB_CALL, r['stubs']['blob'])
    for s in SH.ACTOR_CALLS:
        assert struct.unpack_from('<I', t, s)[0] == A.bl(s, r['stubs']['actor'])
    for st in SH.FIELD_CALLS:
        assert struct.unpack_from('<I', t, st)[0] == A.bl(st, r['stubs']['field'])
    assert struct.unpack_from('<I', t, SH.DEFER_ALLOC)[0] == A.bl(SH.DEFER_ALLOC, r['stubs']['alloc'])
    assert struct.unpack_from('<I', t, SH.DEFERRED_IN)[0] == A.b(SH.DEFERRED_IN, r['stubs']['skip'])
    for st in SH.WORLD_CALLS:
        assert struct.unpack_from('<I', t, st)[0] == A.bl(st, r['stubs']['world'])
    assert struct.unpack_from('<I', t, SH.WCAM_CALL)[0] == A.bl(SH.WCAM_CALL, r['stubs']['wcam'])
    assert struct.unpack_from('<I', t, SH.WBLOB_CALL)[0] == A.bl(SH.WBLOB_CALL, r['stubs']['wblob'])
    assert r['bytes'] <= 0x3800                      # the 14 KB part (667d)
    with pytest.raises(ValueError):
        SH.apply_to_nso(str(out), str(tmp_path / 'again'), stock)



def _run_stub(m, at, words, regs):
    from unicorn import arm64_const as R
    m.uc.mem_write(at, b''.join(struct.pack('<I', w) for w in words))
    for k, v in regs.items():
        m.uc.reg_write(getattr(R, 'UC_ARM64_REG_' + k), v)
    m.uc.reg_write(R.UC_ARM64_REG_LR, RET)
    return R


def test_queued_blob_noted_captured_marked_and_skipped():
    # BUILD 645f: the blob is queued, not drawn; it must leave no state behind
    m = Machine()
    ENT = RB + 0x2000
    at = CODE + 0x8000
    R = _run_stub(m, at, SH.alloc_stub(at, CTX), {'X0': ENT, 'X20': 0})
    for mode, noted in ((0, 0), (SH.MODE_ACTOR, 0), (SH.MODE_BLOB, ENT)):
        m.uc.mem_write(CTX + SH.CTX_ENTRY, bytes(8))
        m.u32(CTX, mode)
        m.uc.reg_write(R.UC_ARM64_REG_LR, RET)
        m.uc.emu_start(at, RET)
        assert m.uc.reg_read(R.UC_ARM64_REG_X20) == ENT          # the move still happens
        got = struct.unpack('<Q', bytes(m.uc.mem_read(CTX + SH.CTX_ENTRY, 8)))[0]
        assert got == noted, mode
    # field_78 fills the entry: a matrix of its own
    blob = np.diag([300.0, 1.0, 300.0, 1.0])
    blob[3, :3] = (50.0, 900.0, 4000.0)
    m.u32(ENT + SH.DE_HASMAT, 1)
    m.mat(ENT + 0x14, blob)
    m.u32(CTX + 4, 0)
    m.call('sh_blob_done', CTX)
    assert m.u32(CTX + 4) == 1                                   # have_blob
    assert np.allclose(m.mat(CTX + 16), blob)
    assert m.u32(ENT + SH.DE_HASMAT) == SH.DE_SKIP               # marked
    assert bytes(m.uc.mem_read(CTX + SH.CTX_ENTRY, 8)) == bytes(8)
    # an entry without a matrix is left alone (blob drawn, no shadow)
    m.uc.mem_write(CTX + SH.CTX_ENTRY, struct.pack('<Q', ENT))
    m.u32(ENT + SH.DE_HASMAT, 0)
    m.u32(CTX + 4, 0)
    m.call('sh_blob_done', CTX)
    assert m.u32(CTX + 4) == 0 and m.u32(ENT + SH.DE_HASMAT) == 0
    # no entry noted (the blob was drawn at once): nothing happens
    m.call('sh_blob_done', CTX)
    assert m.u32(CTX + 4) == 0


def test_skip_stub_returns_only_on_a_marked_entry():
    m = Machine()
    ENT = RB + 0x2000
    # lay the stub out at its real distance from draw_deferred's labels
    at = 0x0FFF000
    base = 0x0FF0000
    m.uc.mem_map(base, 0x200000)
    w = SH.skip_stub(at)
    for va in (SH.DEFERRED_IN + 4, SH.DEFERRED_OUT):
        m.uc.mem_write(va, struct.pack('<I', 0xD65F03C0))            # ret
    from unicorn import arm64_const as R
    m.uc.mem_write(at, b''.join(struct.pack('<I', x) for x in w))
    for flag, out in ((SH.DE_SKIP, SH.DEFERRED_OUT), (1, SH.DEFERRED_IN + 4),
                      (0, SH.DEFERRED_IN + 4)):
        m.u32(ENT + SH.DE_HASMAT, flag)
        m.uc.reg_write(R.UC_ARM64_REG_X0, ENT)
        m.uc.emu_start(at, out, count=len(w))
        assert m.uc.reg_read(R.UC_ARM64_REG_PC) == out, flag
        assert m.uc.reg_read(R.UC_ARM64_REG_X21) == ENT



def _persp(n=10.0, f=100000.0, fov=1.0):
    t = 1.0 / np.tan(fov / 2)
    M = np.array([[t, 0, 0, 0], [0, t, 0, 0],
                  [0, 0, (f + n) / (n - f), 2 * f * n / (n - f)], [0, 0, -1, 0]])
    return M.T                                              # row vectors


def _shadow(bias, view, proj, blob, part):
    m = Machine()
    m.mat(RS + 0x28, blob)
    m.u32(CTX, SH.MODE_BLOB)
    m.call('sh_pre', CTX, RS)
    m.mat(RS + 0x28, part)
    m.mat(RS + 0x68, view)
    m.mat(RS + 0xA8, proj)
    m.uc.mem_write(CTX + SH.CTX_BIAS, struct.pack('<f', bias))
    m.u32(CTX, SH.MODE_ACTOR)
    m.call('sh_begin', CTX, RS, RB)
    return m.mat(RS + 0x28).astype(np.float64)


def test_shadow_moved_toward_the_eye_not_across_the_screen():
    # BUILD 645g: the depth bias. Eye at the origin looking down -z, a floor
    # 900 below it, 4000 ahead; a disc of radius 300.
    view = np.eye(4)
    view[3, :3] = (30.0, -50.0, 20.0)                      # eye at (-30, 50, -20)
    proj = _persp()
    blob = np.diag([300.0, 1.0, 300.0, 1.0])
    blob[3, :3] = (50.0, -900.0, -4000.0)
    part = rot(1, 0.4)
    part[3, :3] = (60.0, -500.0, -3950.0)
    flat0 = _shadow(0.0, view, proj, blob, part)
    flat = _shadow(SH.BIAS, view, proj, blob, part)
    C = view @ proj
    pts = np.c_[np.random.default_rng(2).uniform(-150, 150, (40, 3)), np.ones(40)]
    w0 = pts @ flat0 @ C
    w1 = pts @ flat @ C
    shift = (pts @ flat - pts @ flat0)[:, :3]
    # every point moved by the same world vector, BIAS radii long, toward the eye
    assert np.allclose(shift, shift[0], atol=1e-2)
    assert abs(np.linalg.norm(shift[0]) - SH.BIAS * 300) < 0.5
    eye = np.array([-30.0, 50.0, -20.0])
    q = blob[3, :3]
    dirn = (eye - q) / np.linalg.norm(eye - q)
    assert shift[0] @ dirn > 0.999 * np.linalg.norm(shift[0])
    # nearer in depth, (all but) unmoved on screen
    assert np.all(w1[:, 2] / w1[:, 3] < w0[:, 2] / w0[:, 3])
    sx0, sx1 = w0[:, :2] / w0[:, 3:4], w1[:, :2] / w1[:, 3:4]
    assert np.max(np.abs(sx1 - sx0)) < 2e-3                # < 1 px at 1080p


def test_ortho_view_still_biased_toward_the_viewer():
    view = np.eye(4)
    proj = np.diag([1 / 2000.0, 1 / 2000.0, -1 / 50000.0, 1.0])  # depth: -z is far
    blob = np.diag([300.0, 1.0, 300.0, 1.0])
    blob[3, :3] = (0.0, -900.0, -4000.0)
    part = np.eye(4)
    part[3, :3] = (0.0, -500.0, -4000.0)
    flat0 = _shadow(0.0, view, proj, blob, part)
    flat = _shadow(SH.BIAS, view, proj, blob, part)
    p = np.array([10.0, 20.0, 30.0, 1.0])
    z0 = (p @ flat0 @ view @ proj)[2]
    z1 = (p @ flat @ view @ proj)[2]
    assert z1 < z0
    assert np.allclose((p @ flat0 @ view @ proj)[:2], (p @ flat @ view @ proj)[:2], atol=1e-6)



def test_each_later_draw_steps_back():
    # BUILD 645h: the speckle. Cull back faces when the model does not cull,
    # and every later shadow draw of the actor sits a step farther back.
    view = np.eye(4)
    proj = _persp()
    blob = np.diag([300.0, 1.0, 300.0, 1.0])
    blob[3, :3] = (50.0, -900.0, -4000.0)
    part = np.eye(4)
    part[3, :3] = (60.0, -500.0, -3950.0)
    m = Machine()
    m.mat(RS + 0x28, blob)
    m.u32(CTX, SH.MODE_BLOB)
    m.call('sh_pre', CTX, RS)
    m.mat(RS + 0x68, view)
    m.mat(RS + 0xA8, proj)
    m.uc.mem_write(CTX + SH.CTX_BIAS, struct.pack('<f', SH.BIAS))
    m.u32(CTX, SH.MODE_ACTOR)
    C = view @ proj
    p = np.array([5.0, 7.0, 9.0, 1.0])
    zs = []
    for n in range(70):
        m.mat(RS + 0x28, part)
        m.u32(RB + 0x764, 0)
        m.call('sh_begin', CTX, RS, RB)
        assert m.u32(RB + 0x764) == 0                          # the model's own (645l)
        c = p @ m.mat(RS + 0x28).astype(np.float64) @ C
        zs.append(c[2] / c[3])
        m.call('sh_end', CTX, RS, RB)
        assert m.u32(RB + 0x764) == 0                          # restored
    zs = np.array(zs)
    assert np.all(np.diff(zs[:64]) > 0)                        # each one farther
    assert np.allclose(zs[63:], zs[63])                        # capped: never behind the floor
    # the step is well above float rounding of the depth
    print("step", np.min(np.diff(zs[:64])))
    assert np.min(np.diff(zs[:64])) > 1e-7
    # a new actor (its blob) starts again at the front
    m.mat(RS + 0x28, blob)
    m.u32(CTX, SH.MODE_BLOB)
    m.call('sh_pre', CTX, RS)
    m.u32(CTX, SH.MODE_ACTOR)
    m.mat(RS + 0x28, part)
    m.call('sh_begin', CTX, RS, RB)
    c = p @ m.mat(RS + 0x28).astype(np.float64) @ C
    assert abs(c[2] / c[3] - zs[0]) < 1e-9




def _blend(src, dst, sf, eq, df):
    """GL blend of one RGBA fragment, the port's factor/equation indices."""
    def f(i):
        return {0: np.zeros(4), 1: np.ones(4), 4: np.full(4, src[3]),
                5: np.full(4, 1 - src[3]), 6: np.full(4, dst[3]),
                7: np.full(4, 1 - dst[3]), 8: dst.copy()}[i]
    if eq == 2:
        out = np.minimum(src, dst)
    elif eq == 4:
        out = dst * f(df) - src * f(sf)
    else:
        out = src * f(sf) + dst * f(df)
    return np.clip(out, 0, 1)


def _state(m):
    return (m.u32(RB + 0x740) & 0xF, m.u32(RB + 0x76C), m.u32(RB + 0x770),
            m.u32(RB + 0x774))


def test_alpha_marks_each_pixel_once_in_any_order():
    # BUILD 645o: ONE draw per shadow; the real block values through a GL
    # blend model, 1..6 stacked shadow draws x 1..3 faces each, on an opaque
    # floor pixel (alpha 1), with the shader's grey SHADOW_KEEP
    m = Machine()
    blob = np.diag([300.0, 1.0, 300.0, 1.0])
    blob[3, :3] = (0.0, -900.0, -4000.0)
    m.mat(RS + 0x28, blob)
    m.u32(CTX, SH.MODE_BLOB)
    m.call('sh_pre', CTX, RS)
    m.u32(CTX, SH.MODE_ACTOR)
    m.mat(RS + 0x28, np.eye(4))
    m.uc.mem_write(RB + 0x740, bytes(0x38))
    m.u32(RB + 0x740, 0x12345670)
    m.call('sh_begin', CTX, RS, RB)
    st = _state(m)
    assert st == (0xF, 8, 0, 7)                                 # DST_COLOR, ADD, 1-DA
    assert m.u32(RB + 0x744) == 0 and m.u32(RB + 0x748) == 1    # LESS, no depth write
    assert m.u32(RB + 0x74C) == 0                               # no stencil
    assert m.u32(RB + 0x740) >> 4 == 0x1234567                  # other bits kept
    m.call('sh_end', CTX, RS, RB)
    assert m.u32(RB + 0x740) == 0x12345670
    keep = float(SH.SHADOW_KEEP)
    src = np.full(4, keep)
    for floor in (np.array([0.30, 0.70, 0.20, 1.0]), np.array([1.0, 1.0, 1.0, 1.0]),
                  np.array([0.02, 0.5, 0.9, 1.0])):
        for draws in range(1, 19):                             # layers x faces
            px = floor.copy()
            for _ in range(draws):
                mask, sf, eq, df = st
                px = _blend(src, px, sf, eq, df)
                px = np.round(px * 255) / 255                  # an 8-bit target
            assert np.allclose(px[:3], floor[:3] * keep, atol=2.5 / 255), (draws, px)
            assert abs(px[3] - keep) <= 1.5 / 255



def test_tiny_parts_cast_no_shadow():
    # BUILD 645p: fewer than 12 indices (4 triangles): drawn, no shadow draw
    m = Machine()
    m.mat(RS + 0x28, np.eye(4))
    m.u32(CTX, SH.MODE_BLOB)
    assert m.call('sh_pre', CTX, RS) == 1
    m.u32(CTX, SH.MODE_ACTOR)
    assert m.call('sh_pre', CTX, RS, 11) == 0
    assert m.call('sh_pre', CTX, RS, 12) == 2
    assert m.call('sh_pre', CTX, RS, 3000) == 2


def test_helper_stub_passes_the_index_count():
    import a64 as A
    w = SH.helper_stub(0x99A000, 0x4C1F350, {k: 0x99B000 + 0x100 * i for i, k in
                                             enumerate(('sh_pre', 'sh_begin', 'sh_end'))})
    i = w.index(A.bl(0x99A000 + 4 * w.index(A.ldr(2, 31, 0x38)) + 4, 0x99B000))
    assert w[i - 1] == A.ldr(2, 31, 0x38)                     # saved x5 -> w2
    assert A.stp64_off(4, 5, 31, 0x30) in w                    # x5 saved at sp+0x38


def test_fast_path_reissues_the_draw_with_the_shadow_uniforms():
    # BUILD 645q: sh_begin(..., fast) -- a fake GL device and uniform ring,
    # every vtable call recorded by a code hook
    from unicorn import UC_HOOK_CODE
    from unicorn import arm64_const as R
    m = Machine()
    FVT, FFN, RING, RGOT, FGOT, FRM, BUF, BVT, FAST = (
        0xB000, 0xB400, 0x100000, 0xC000, 0xC010, 0xC020, 0xC100, 0xC200, 0xC300)
    m.uc.mem_map(RING, 0x10000)
    q = lambda a, v: m.uc.mem_write(a, struct.pack('<Q', v))      # noqa: E731
    q(DEV, W); q(W, OBJ); q(OBJ, FVT)
    names = {0x108: 'depth', 0x118: 'blend', 0xD0: 'ubo', 0x150: 'draw', 0x128: 'cull'}
    for k, (off, _n) in enumerate(names.items()):
        q(FVT + off, FFN + 4 * k)
    q(BUF, BVT); q(BVT + 0x58, FFN + 0x40); q(BVT + 0x38, FFN + 0x44)
    m.uc.mem_write(FFN, struct.pack('<I', 0xD65F03C0) * 32)        # ret
    fi = 1
    q(RGOT, RING); m.u32(FGOT, fi)                 # the globals the GOT points at
    m.u32(RING + 0x4030 + 4 * fi, 7)                               # slot 7
    q(RING + (fi << 11) + 8 * 7 + 0x10, BUF)
    calls, wrote = [], []
    rev = {FFN + 4 * k: n for k, n in enumerate(names.values())}

    def hook(uc, addr, size, data):
        regs = [uc.reg_read(getattr(R, 'UC_ARM64_REG_X%d' % i)) for i in range(8)]
        if addr in rev:
            calls.append((rev[addr],) + tuple(regs[:8]))
        elif addr == FFN + 0x40:
            uc.reg_write(R.UC_ARM64_REG_X0, 0x50)                  # buffer size
        elif addr == FFN + 0x44:
            wrote.append((regs[0], bytes(uc.mem_read(regs[1], regs[2])), regs[3]))
    m.uc.hook_add(UC_HOOK_CODE, hook, begin=FFN, end=FFN + 0x80)
    view, proj = np.eye(4), _persp()
    blob = np.diag([300.0, 1.0, 300.0, 1.0])
    blob[3, :3] = (50.0, -900.0, -4000.0)
    part = rot(1, 0.4)
    part[3, :3] = (60.0, -500.0, -3950.0)
    m.mat(RS + 0x28, blob)
    m.u32(CTX, SH.MODE_BLOB)
    m.call('sh_pre', CTX, RS)
    m.u32(CTX, SH.MODE_ACTOR)
    m.mat(RS + 0x28, part)
    m.mat(RS + 0x68, view)
    m.mat(RS + 0xA8, proj)
    m.u32(RS + 0x0C, 4)
    m.uc.mem_write(CTX + SH.CTX_BIAS, struct.pack('<f', SH.BIAS))
    # 0x740 mask F, 0x744 write 1, 0x748 LEQUAL, 0x768 front 1, 0x76C ONE / ADD / ZERO
    rb0 = struct.pack('<14I', 0xF, 1, 3, 0, 0, 0, 0, 0, 0, 0, 1, 1, 0, 0)
    m.uc.mem_write(RB + 0x740, rb0)
    fast = (36, 0, 0x1230, 4, 0xDEAD000, RGOT, FGOT)
    m.uc.mem_write(FAST, struct.pack('<7Q', *fast))
    rs0 = bytes(m.uc.mem_read(RS, 0x100))
    assert m.call('sh_begin', CTX, RS, RB, DEV, FAST) == 1
    # rs and the block untouched; the ring advanced
    assert bytes(m.uc.mem_read(RS, 0x100)) == rs0
    assert bytes(m.uc.mem_read(RB + 0x740, 0x38)) == rb0
    assert m.u32(RING + 0x4030 + 4 * fi) == 8
    seq = [c[0] for c in calls]
    assert seq == ['depth', 'blend', 'ubo', 'cull', 'draw', 'cull', 'depth', 'blend'], seq
    assert calls[0][1:6] == (OBJ, 0, 1, 0, 0)                      # LESS, no write
    assert calls[1][1:6] == (OBJ, 0, 8, 0, 7)                      # DST_COLOR, ADD, 1-DA
    assert calls[2][1:9] == (OBJ, 0, 1, BUF, 0, 0, 0x50, 0)
    assert calls[3][1:4] == (OBJ, 0, 1)                            # 667: cull off, front kept
    assert calls[4][1:7] == (OBJ,) + fast[:5]                      # the same draw
    assert calls[5][1:4] == (OBJ, 0, 1)                            # the block's cull back
    assert calls[6][1:6] == (OBJ, 1, 3, 0, 0)                      # restored
    assert calls[7][1:6] == (OBJ, 0, 1, 0, 0)
    # the uniform block: blend mode 5, then model' . view . proj -- the same
    # matrix the slow path would have put in rs+0x28 times view . proj
    (buf, data, off), = wrote
    assert buf == BUF and off == 0 and len(data) == 0x50
    assert struct.unpack('<i', data[:4])[0] == 5 and data[4:16] == bytes(12)
    got = np.frombuffer(data[16:80], '<f4').reshape(4, 4)
    m.u32(CTX + 0xDC, 0)                                           # same layer step
    flat = _shadow(SH.BIAS, view, proj, blob, part)
    assert np.allclose(got, flat @ view @ proj, rtol=1e-5, atol=1e-3)



def _fast_block(field, cam=None):
    """The fast path's uniform block (0x50 bytes) for one part."""
    from unicorn import UC_HOOK_CODE
    from unicorn import arm64_const as R
    m = Machine()
    FVT, FFN, RING, RGOT, FGOT, BUF, BVT, FAST, CAM = (
        0xB000, 0xB400, 0x100000, 0xC000, 0xC010, 0xC100, 0xC200, 0xC300, 0xC800)
    m.uc.mem_map(RING, 0x10000)
    q = lambda a, v: m.uc.mem_write(a, struct.pack('<Q', v))      # noqa: E731
    q(DEV, W); q(W, OBJ); q(OBJ, FVT)
    for k, off in enumerate((0x108, 0x118, 0xD0, 0x150, 0x128)):
        q(FVT + off, FFN + 4 * k)
    q(BUF, BVT); q(BVT + 0x58, FFN + 0x40); q(BVT + 0x38, FFN + 0x44)
    m.uc.mem_write(FFN, struct.pack('<I', 0xD65F03C0) * 32)
    q(RGOT, RING); m.u32(FGOT, 1)
    q(RING + (1 << 11) + 8 * 0 + 0x10, BUF)
    wrote = []

    def hook(uc, addr, size, data):
        regs = [uc.reg_read(getattr(R, 'UC_ARM64_REG_X%d' % i)) for i in range(4)]
        if addr == FFN + 0x40:
            uc.reg_write(R.UC_ARM64_REG_X0, 0x50)
        elif addr == FFN + 0x44:
            wrote.append(bytes(uc.mem_read(regs[1], regs[2])))
    m.uc.hook_add(UC_HOOK_CODE, hook, begin=FFN, end=FFN + 0x80)
    blob = np.diag([300.0, 1.0, 300.0, 1.0])
    blob[3, :3] = (50.0, -900.0, -4000.0)
    part = rot(1, 0.4)
    part[3, :3] = (60.0, -500.0, -3950.0)
    m.mat(RS + 0x28, blob)
    m.u32(CTX, SH.MODE_BLOB)
    m.call('sh_pre', CTX, RS)
    m.u32(CTX, SH.MODE_ACTOR)
    m.mat(RS + 0x28, part)
    m.mat(RS + 0x68, np.eye(4))
    m.mat(RS + 0xA8, _persp())
    m.u32(RS + 0x0C, 4)
    m.uc.mem_write(CTX + SH.CTX_BIAS, struct.pack('<f', SH.BIAS))
    m.uc.mem_write(RB + 0x740, struct.pack('<14I', 0xF, 1, 3, 0, 0, 0, 0, 0, 0, 0, 1, 1, 0, 0))
    m.uc.mem_write(FAST, struct.pack('<7Q', 36, 0, 0x1230, 4, 0xDEAD000, RGOT, FGOT))
    m.u32(CTX + SH.CTX_FIELD, field)
    if cam is not None:
        flag, dflag, tset, tx, ty = cam
        m.uc.mem_write(CAM, struct.pack('<I', flag))
        m.uc.mem_write(CAM + 0x24, struct.pack('<2fI', tx, ty, tset))
        m.uc.mem_write(CAM + 0x3C, struct.pack('<I', dflag))
        q(CTX + SH.CTX_CAM, CAM)
    assert m.call('sh_begin', CTX, RS, RB, DEV, FAST) == 1
    return np.frombuffer(wrote[0][16:80], '<f4').reshape(4, 4)


def test_670_fast_shadow_takes_the_subcam_shift():
    """670: the fast shadow block bypasses the draw join where ff7nx_subcam
    shifts the field scene -- it gets the same clip shift (it wobbled)."""
    import ff7nx_subcam as SC
    assert (SC.S_FLAG, SC.S_T, SC.S_TSET, SC.S_DEF) == (0x00, 0x24, 0x2C, 0x3C)
    base = _fast_block(1)
    tx, ty = -0.0125, 0.02
    for cam in ((1, 0, 1, tx, ty), (0, 1, 1, tx, ty)):          # scene / its deferred group
        got = _fast_block(1, cam)
        want = base.copy()
        want[:, 0] += tx * base[:, 3]
        want[:, 1] += ty * base[:, 3]
        assert np.allclose(got, want, rtol=1e-5, atol=1e-6)
    for cam in ((0, 0, 1, tx, ty), (1, 0, 0, tx, ty)):          # no move / no shift yet
        assert np.allclose(_fast_block(1, cam), base)
    assert np.allclose(_fast_block(0, (1, 0, 1, tx, ty)), _fast_block(0))   # battle: never


# --- BUILD 646j: field models are drawn in CAMERA space -------------------
# 0x639157: C = the field camera (column vectors, v = A w + T); 0x6392BB:
# struc_110.position = C . entity position, struc_110.matrix = (C Rz S)^T; so
# a part matrix (row vectors, rs +0x28) maps model space to camera space.
PT, CAM_PAGE, LVL_PAGE, S110_PAGE = 0x200000, 0x300000, 0x310000, 0x320000
LEVEL, S110 = 0x00200000, 0x00400040                    # guest addresses
FFNX_LIGHT = np.array([0.4330127, -0.25, 0.8660254])    # towards the light, world


def _camera(pitch=1.0, yaw=0.6, t=(30.0, -50.0, 4000.0)):
    """a field camera looking down at the world (Z up): 4x4, column vectors"""
    cz, sz = np.cos(yaw), np.sin(yaw)
    cx, sx = np.cos(pitch), np.sin(pitch)
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    c = np.eye(4)
    c[:3, :3] = rx @ rz
    c[:3, 3] = t
    return c


def _field_world(m, tris, cam, origin, mapped=True, access=None):
    """guest memory for a field: page table, camera, field file + walkmesh
    `tris` (3 x (x, y, z) each), struc_110 at S110 with position = C . origin.
    Returns the page table's host address."""
    for a in (PT, CAM_PAGE, LVL_PAGE, S110_PAGE):
        m.uc.mem_map(a, 0x10000)
    q = lambda a, v: m.uc.mem_write(a, struct.pack('<Q', v))   # noqa: E731
    if mapped:
        q(PT + 8 * 0xCFF, CAM_PAGE)
    for k in range(4):
        q(PT + 8 * ((LEVEL >> 12) + k), LVL_PAGE + 0x1000 * k)
    q(PT + 8 * (S110 >> 12), S110_PAGE)
    m.uc.mem_write(CAM_PAGE + 0x594, struct.pack('<I', LEVEL))
    m.uc.mem_write(CAM_PAGE + 0x980, np.asarray(cam, '<f4').tobytes())
    wo = 0x40
    body = struct.pack('<IH', 0, len(tris)) + b'\0\0'
    for t in tris:
        for x, y, z in t:
            body += struct.pack('<4h', x, y, z, 0)
    for i in range(len(tris)):                     # access: neighbours (651)
        nb = list((access or {}).get(i, ())) + [-1, -1, -1]
        body += struct.pack('<3h', *nb[:3])
    m.uc.mem_write(LVL_PAGE + 0x16, struct.pack('<I', wo))
    m.uc.mem_write(LVL_PAGE + wo, body)
    _place(m, cam, origin)
    return PT


def _place(m, cam, origin):
    pos = (cam @ np.r_[origin, 1.0])[:3]
    m.uc.mem_write(S110_PAGE + (S110 & 0xFFF) + 4, np.asarray(pos, '<f4').tobytes())


def _part(cam, w):
    """a part matrix (row vectors) whose origin is world point w, axes the
    camera's -- what draw_3d_model hands the renderer on the field"""
    p = np.eye(4)
    p[:3, :3] = cam[:3, :3].T
    p[3, :3] = (cam @ np.r_[w, 1.0])[:3]
    return p


def _field_draw(m, parts, pages, key=S110):
    m.call('sh_field', CTX, key, pages)
    m.u32(CTX, SH.MODE_ACTOR)
    got = []
    for p in parts:
        m.mat(RS + 0x28, p)
        a = m.call('sh_pre', CTX, RS)
        got.append(a)
        if a == 2:
            m.call('sh_begin', CTX, RS, RB)
            got.append(m.mat(RS + 0x28).astype(np.float64))
            m.call('sh_end', CTX, RS, RB)
    m.u32(CTX, 0)
    return got


def _landed(cam, flat, pts_model):
    """world positions of model-space points after the shadow matrix"""
    v = np.c_[pts_model, np.ones(len(pts_model))] @ flat           # camera space
    return (np.linalg.inv(cam) @ v.T).T[:, :3]


SLOPE = [((0, 0, 100), (1000, 0, 200), (0, 1000, 100)),
         ((1000, 0, 200), (1000, 1000, 200), (0, 1000, 100))]


def test_field_shadow_lies_on_the_walkmesh_in_world_space():
    cam = _camera()
    for x, floor in ((250, 125.0), (500, 150.0), (900, 190.0)):
        m = Machine()
        origin = np.array([x, 300.0, floor - 10])                 # entity z - 10
        pages = _field_world(m, SLOPE, cam, origin)
        hips = origin + (0, 0, 80)
        got = _field_draw(m, [_part(cam, hips)], pages)
        assert got[0] == 2
        # points of the part, in model space (the part's axes are the world's)
        pts = np.random.default_rng(1).uniform(-30, 30, (16, 3))
        world = _landed(cam, got[1], pts)
        assert np.allclose(world[:, 2], floor - 11, atol=1e-2), (x, world[:, 2])  # FFNx -11
        # each point moved along FFNx's light onto the floor
        src = pts + hips
        drop = src - world
        along = drop / drop[:, 2:3]
        assert np.allclose(along, FFNX_LIGHT / FFNX_LIGHT[2], atol=1e-3)


def test_field_shadow_ignores_the_camera():
    # the same world scene through three cameras lands on the same floor spot
    spots = []
    for pitch, yaw, t in ((1.0, 0.6, (30, -50, 4000)), (0.7, -1.9, (0, 0, 2500)),
                          (1.3, 2.8, (-400, 120, 6000))):
        cam = _camera(pitch, yaw, t)
        m = Machine()
        origin = np.array([500.0, 300.0, 140.0])
        pages = _field_world(m, SLOPE, cam, origin)
        got = _field_draw(m, [_part(cam, origin + (0, 0, 80))], pages)
        spots.append(_landed(cam, got[1], np.zeros((1, 3)))[0])
    assert np.allclose(spots[0], spots[1], atol=1e-2) and np.allclose(spots[0], spots[2], atol=1e-2)
    assert abs(spots[0][2] - 139.0) < 1e-2


def test_field_off_the_walkmesh_uses_the_model_origin():
    cam = _camera()
    m = Machine()
    origin = np.array([5000.0, 5000.0, 60.0])
    pages = _field_world(m, SLOPE, cam, origin)
    got = _field_draw(m, [_part(cam, origin + (0, 0, 80))], pages)
    assert abs(_landed(cam, got[1], np.zeros((1, 3)))[0][2] - 60.0) < 1e-2


def test_field_bridge_picks_the_level_nearest_the_model():
    cam = _camera()
    tri = lambda z: ((0, 0, z), (1000, 0, z), (0, 1000, z))      # noqa: E731
    for oz, want in ((-10.0, -11.0), (290.0, 289.0)):
        m = Machine()
        origin = np.array([100.0, 100.0, oz])
        pages = _field_world(m, [tri(300), tri(0)], cam, origin)
        got = _field_draw(m, [_part(cam, origin + (0, 0, 80))], pages)
        assert abs(_landed(cam, got[1], np.zeros((1, 3)))[0][2] - want) < 1e-2


def test_703_a_body_hanging_below_its_floor_casts_nothing():
    # southmk2 after the Air Buster: Cloud hangs from the broken bridge, his
    # origin at the walkway (z 491 - 10), his hips far below it, over the
    # walkmesh that still spans the gap. The field's up is +Z (FFNx's
    # receiver is 11 below the walkmesh): a body under the receiver shades
    # nothing (676 used to flip up to -Z and lay him on the invisible floor)
    cam = _camera()
    bridge = ((-1199, -3087, 491), (1409, -3087, 491), (-1199, -2930, 491))
    origin = np.array([0.0, -3050.0, 481.0])
    for drop, cast in ((80, False), (40, False), (17, False),     # hanging
                       (5, True), (0, True)):                      # lying: as before 703
        m = Machine()
        pages = _field_world(m, [bridge], cam, origin)
        got = _field_draw(m, [_part(cam, origin - (0, 0, drop))], pages)
        assert (got[0] == 2) == cast, (drop, got[0])
        if cast and drop == 0:
            world = _landed(cam, got[1], np.zeros((1, 3)))
            assert abs(world[0][2] - 480.0) < 1e-2, (drop, world)
    # standing on the same floor: unchanged
    m = Machine()
    pages = _field_world(m, [bridge], cam, origin)
    got = _field_draw(m, [_part(cam, origin + (0, 0, 80))], pages)
    assert got[0] == 2
    assert abs(_landed(cam, got[1], np.zeros((1, 3)))[0][2] - 480.0) < 1e-2


def test_field_remembers_the_triangle():
    cam = _camera()
    m = Machine()
    origin = np.array([900.0, 300.0, 180.0])                         # in triangle 1
    pages = _field_world(m, SLOPE, cam, origin)
    _field_draw(m, [_part(cam, origin + (0, 0, 80))], pages)
    rows = [struct.unpack('<Ii', bytes(m.uc.mem_read(CTX + 0x110 + 8 * i, 8))) for i in range(16)]
    assert (S110, 1) in rows
    # the walkmesh count read once more, the cached triangle answers first:
    # make triangle 0 cover the point too, at another height -- still 1
    m.uc.mem_write(LVL_PAGE + 0x40 + 8, struct.pack('<4h4h4h', -5000, -5000, 999, 0,
                                                      5000, -5000, 999, 0, -5000, 5000, 999, 0))
    got = _field_draw(m, [_part(cam, origin + (0, 0, 80))], pages)
    assert abs(_landed(cam, got[1], np.zeros((1, 3)))[0][2] - 179.0) < 1e-2


def test_field_without_a_camera_draws_no_shadow():
    cam = _camera()
    m = Machine()
    origin = np.array([500.0, 300.0, 140.0])
    pages = _field_world(m, SLOPE, cam, origin, mapped=False)
    m.call('sh_field', CTX, S110, pages)
    m.u32(CTX, SH.MODE_ACTOR)
    m.mat(RS + 0x28, _part(cam, origin + (0, 0, 80)))
    # 667d: the floor is built in sh_pre -- no camera, no shadow at all
    assert m.call('sh_pre', CTX, RS) == 0
    assert m.u32(CTX + 4) == 0                                        # have_blob
    m.mat(RS + 0x28, _part(cam, origin + (0, 0, 40)))
    assert m.call('sh_pre', CTX, RS) == 0                            # rest: none


def test_field_stub_hands_over_the_guest_struc_and_brackets_the_draw():
    import a64 as A
    at = 0x99B000
    fn = {'sh_field': 0x99C000}
    w = SH.field_stub(at, 0x4C1F350, fn)
    bls = [(i, (x & 0x3FFFFFF)) for i, x in enumerate(w) if x >> 26 == 0x25]
    tg = [at + 4 * i + 4 * (((o ^ 0x2000000) - 0x2000000)) for i, o in bls]
    assert tg == [SH.GUEST_TO_HOST, 0x99C000, SH.MODEL_TARGET]       # 667c: one pass
    assert A.ldr(1, 0, 0) in w                                       # w1 = arg3 (guest)
    assert A.ldr(0, 9, SH.X86_ESP) in w and A.add_imm(0, 0, 12) in w
    assert A.str_(31, 9, SH.CTX_FIELD) in w and w[-1] == A.ret()


def test_ctx_offsets_match_the_c_static_asserts():
    src = open(os.path.join(HERE, 'native', 'shadows.c')).read()
    for name, val in (('CTX_ENTRY', SH.CTX_ENTRY), ('CTX_BIAS', SH.CTX_BIAS),
                      ('CTX_PEND/FASTDONE', SH.CTX_PEND), ('CTX_FIELD', SH.CTX_FIELD)):
        assert ('== 0x%X' % val) in src.replace('0x%x' % val, '0x%X' % val), name
    assert SH.CTX_FASTDONE == SH.CTX_PEND + 4 and SH.STATE_BYTES == 0x390
    assert ('OFF(cam) == 0x%X' % SH.CTX_CAM) in src                  # 670


def test_field_shadows_use_blend_mode_6_battle_keeps_5():
    cam = _camera()
    m = Machine()
    origin = np.array([500.0, 300.0, 140.0])
    pages = _field_world(m, SLOPE, cam, origin)
    m.call('sh_field', CTX, S110, pages)
    m.u32(CTX, SH.MODE_ACTOR)
    m.mat(RS + 0x28, _part(cam, origin + (0, 0, 80)))
    assert m.call('sh_pre', CTX, RS) == 2
    m.call('sh_begin', CTX, RS, RB)
    assert struct.unpack('<i', bytes(m.uc.mem_read(RS + 0x0C, 4)))[0] == 6
    m.call('sh_end', CTX, RS, RB)
    m.u32(CTX + SH.CTX_FIELD, 0)                                 # as field_stub
    m.u32(CTX, SH.MODE_BLOB)
    m.mat(RS + 0x28, np.eye(4))
    m.call('sh_pre', CTX, RS)
    m.u32(CTX, SH.MODE_ACTOR)
    m.mat(RS + 0x28, np.eye(4) + np.diag([0, 0, 0, 0]) + np.outer([0, 0, 0, 1], [0, 50, 0, 0]))
    assert m.call('sh_pre', CTX, RS) == 2
    m.call('sh_begin', CTX, RS, RB)
    assert struct.unpack('<i', bytes(m.uc.mem_read(RS + 0x0C, 4)))[0] == 5


def test_shader_line_replaces_the_645p_line(tmp_path):
    src = os.path.join(HERE, 'shaders', 'wide_screen', 'lmain_vv.glsl')
    old = open(src).read().replace(SH.SHADER_ANCHOR,
                                   SH.SHADER_ANCHOR + '\n' + SH.OLD_SHADER_LINE_645P, 1)
    p = tmp_path / 'lmain_vv.glsl'
    p.write_text(old)
    assert SH.patch_shader(str(p))
    t = p.read_text()
    assert t.count('blendMode.x == 6 ? %s : 0.50' % SH.FIELD_KEEP) == 1 and 'ff7nx_shadows 645p' not in t


# --- BUILD 646l: FFNx's battle light, fixed in the battle world -------------
X86C, BPAGE, FPAGE = 0x330000, 0x340000, 0x350000
EBP = 0x00120400


def _battle_guest(m, facings):
    """x86 context (ebp), the actor index at [ebp+8] and facings at
    0xBE12D8 + i * 0x1AEC; returns the page table"""
    for a in (PT, X86C, BPAGE, FPAGE, FPAGE + 0x10000):
        try:
            m.uc.mem_map(a, 0x10000)
        except Exception:
            pass
    q = lambda a, v: m.uc.mem_write(a, struct.pack('<Q', v))   # noqa: E731
    q(PT + 8 * (EBP >> 12), BPAGE)
    for i, f in enumerate(facings):
        g = 0xBE12D8 + i * 0x1AEC
        q(PT + 8 * (g >> 12), FPAGE + 0x1000 * ((g >> 12) - (0xBE12D8 >> 12)))
        m.uc.mem_write(FPAGE + 0x1000 * ((g >> 12) - (0xBE12D8 >> 12)) + (g & 0xFFF),
                       struct.pack('<h', f))
    m.uc.mem_write(X86C + 0x14, struct.pack('<I', EBP))
    return PT


def _rot_y_rows(f):
    a = f * 2 * np.pi / 4096
    c, s_ = np.cos(a), np.sin(a)
    r = np.eye(4)
    r[0, :3] = (c, 0, -s_)
    r[2, :3] = (s_, 0, c)
    return r


def _battle_cam():
    c = np.eye(4)
    c[:3, :3] = (rot(0, 0.4) @ rot(1, 2.2))[:3, :3]
    c[3, :3] = (100.0, -300.0, 5000.0)
    return c


def test_battle_light_is_ffnx_and_the_same_for_every_facing():
    cam = _battle_cam()
    drops = []
    for idx, f in ((0, 0), (1, 2048), (2, 700), (3, -1500)):
        m = Machine()
        pages = _battle_guest(m, [0, 2048, 700, -1500])
        m.uc.mem_write(BPAGE + (EBP & 0xFFF) + 8, struct.pack('<I', idx))
        pos = np.array([300.0 * idx, 0.0, -200.0])
        t = np.eye(4); t[3, :3] = pos
        blob = np.diag([90.0, 1.0, 70.0, 1.0]) @ _rot_y_rows(f) @ t @ cam
        m.u32(CTX, SH.MODE_BLOB)
        m.mat(RS + 0x28, blob)
        assert m.call('sh_pre', CTX, RS) == 1
        m.call('sh_blob_done', CTX, X86C, pages)
        assert m.u32(CTX + 0x10C) == 1                               # facing read
        m.u32(CTX, SH.MODE_ACTOR)
        part = _rot_y_rows(f) @ t @ cam                              # the model faces f too
        m.mat(RS + 0x28, part)
        assert m.call('sh_pre', CTX, RS) == 2
        m.call('sh_begin', CTX, RS, RB)
        flat = m.mat(RS + 0x28).astype(np.float64)
        m.call('sh_end', CTX, RS, RB)
        src_local = np.array([[0.0, -120.0, 0.0, 1.0]])            # 120 up (battle Y down)
        src = (src_local @ part @ np.linalg.inv(cam))[0, :3]
        landed = (src_local @ flat @ np.linalg.inv(cam))[0, :3]
        assert abs(landed[1] - 0.0) < 1e-2                           # on the floor
        drops.append(src - landed)
    want = np.array([0.4330127, -0.8660254, -0.25])
    for d in drops:
        assert np.allclose(d / np.linalg.norm(d), want / np.linalg.norm(want), atol=2e-4), d


def test_battle_without_a_facing_keeps_the_old_slant():
    m = Machine()
    m.u32(CTX, SH.MODE_BLOB)
    m.mat(RS + 0x28, np.eye(4))
    m.call('sh_pre', CTX, RS)
    m.call('sh_blob_done', CTX, 0, 0)
    assert m.u32(CTX + 0x10C) == 0


def test_shadows_bin_is_built_from_this_shadows_c():
    import hashlib
    src = open(os.path.join(HERE, 'native', 'shadows.c'), 'rb').read()
    want = open(os.path.join(HERE, 'native', 'shadows.src.sha256')).read().split()[0]
    assert hashlib.sha256(src).hexdigest() == want, 'run native/build_shadows.sh'


# --- BUILD 647: world-map models ---------------------------------------------
WEBP, ENTRY, WOBJ = 0x00130800, 0x00E2C808, 0x00500100       # guest
WPAGE, EPAGE, OPAGE, CPAGE = 0x360000, 0x370000, 0x380000, 0x390000
WKEY = ENTRY + 0x44


def _world_guest(m, cam_rot, model_c, model_id, origin_cam):
    """x86 ctx (ebp), [ebp+8] = entry, [ebp+0xC] = world object (+0x50 id),
    the model camera C (float, column vectors) at ebp - 0xF8, the world
    camera rotation (s16 / 4096) at 0xDFC448, struc_110 position at
    entry + 0x48. Returns the page table."""
    for a in (PT, X86C, WPAGE, EPAGE, OPAGE, CPAGE):
        try:
            m.uc.mem_map(a, 0x10000)
        except Exception:
            pass
    q = lambda a, v: m.uc.mem_write(a, struct.pack('<Q', v))   # noqa: E731
    q(PT + 8 * (WEBP >> 12), WPAGE)
    q(PT + 8 * ((WEBP - 0x1000) >> 12), WPAGE + 0x1000)
    q(PT + 8 * (ENTRY >> 12), EPAGE)
    q(PT + 8 * ((ENTRY >> 12) + 1), EPAGE + 0x1000)
    q(PT + 8 * (WOBJ >> 12), OPAGE)
    q(PT + 8 * (0xDFC448 >> 12), CPAGE)
    g2 = lambda g: (WPAGE + (g & 0xFFF)) if (g >> 12) == (WEBP >> 12) else (WPAGE + 0x1000 + (g & 0xFFF))  # noqa
    m.uc.mem_write(X86C + 0x14, struct.pack('<I', WEBP))
    m.uc.mem_write(g2(WEBP + 8), struct.pack('<I', ENTRY))
    m.uc.mem_write(g2(WEBP + 0xC), struct.pack('<I', WOBJ))
    m.uc.mem_write(OPAGE + (WOBJ & 0xFFF) + 0x50, struct.pack('<B', model_id))
    m.uc.mem_write(g2(WEBP - 0xF8), np.asarray(model_c, '<f4').tobytes())
    m.uc.mem_write(CPAGE + 0x448, struct.pack('<9h', *[int(round(v * 4096)) for v in cam_rot.ravel()]))
    m.uc.mem_write(EPAGE + (ENTRY & 0xFFF) + 0x48, np.asarray(origin_cam, '<f4').tobytes())
    return PT


def _world_setup(tilt=0.0, model_id=0):
    cam = (rot(0, 0.9) @ rot(1, 0.5))[:3, :3]              # world -> camera rotation
    tiltm = rot(0, tilt)[:3, :3]                             # terrain tilt
    c = np.eye(4)
    c[:3, :3] = cam @ tiltm
    origin_cam = np.array([40.0, -20.0, 3000.0])
    c[:3, 3] = origin_cam
    m = Machine()
    pages = _world_guest(m, cam, c, model_id, origin_cam)
    return m, pages, cam, tiltm, c, origin_cam


def test_world_camera_hook_keeps_ground_models_only():
    m, pages, cam, tiltm, c, o = _world_setup(0.2, 0)
    m.call('sh_wcam', CTX, X86C, pages)
    rows = [struct.unpack('<I3f', bytes(m.uc.mem_read(CTX + 0x1B4 + 16 * i, 16))) for i in range(12)]
    row = [r for r in rows if r[0] == WKEY]
    assert row and np.allclose(row[0][1:], c[:3, 1] / np.linalg.norm(c[:3, 1]), atol=1e-5)
    # the same slot now holding the Highwind (3): no row, no shadow
    m.uc.mem_write(OPAGE + (WOBJ & 0xFFF) + 0x50, struct.pack('<B', 3))
    m.call('sh_wcam', CTX, X86C, pages)
    rows = [struct.unpack('<I3f', bytes(m.uc.mem_read(CTX + 0x1B4 + 16 * i, 16))) for i in range(12)]
    assert not [r for r in rows if r[0] == WKEY]
    m.call('sh_world', CTX, WKEY, pages)
    assert m.u32(CTX + 4) == 0                                       # have_blob 0


def test_world_shadow_on_the_tilted_ground_along_ffnx_light():
    for tilt in (0.0, 0.25):
        m, pages, cam, tiltm, c, o = _world_setup(tilt, 19)
        m.call('sh_wcam', CTX, X86C, pages)
        m.call('sh_world', CTX, WKEY, pages)
        assert m.u32(CTX + 4) == 1 and m.u32(CTX + SH.CTX_FIELD) == 2
        m.u32(CTX, SH.MODE_ACTOR)
        up_c = c[:3, 1] / np.linalg.norm(c[:3, 1])
        part = np.eye(4)
        part[3, :3] = o + 70 * up_c                                  # hips above the feet
        m.mat(RS + 0x28, part)
        assert m.call('sh_pre', CTX, RS) == 2
        m.call('sh_begin', CTX, RS, RB)
        assert struct.unpack('<i', bytes(m.uc.mem_read(RS + 0x0C, 4)))[0] == 5   # 647d: world keeps 0.50
        flat = m.mat(RS + 0x28).astype(np.float64)
        pts = np.c_[np.random.default_rng(4).uniform(-15, 15, (10, 3)), np.ones(10)]
        src = (pts @ part)[:, :3]
        out = (pts @ flat)[:, :3]
        assert np.allclose((out - o) @ up_c, 0.0, atol=1e-2)         # on the ground plane
        light = cam @ np.array([0.4330127, 0.8660254, 0.25])
        d = src - out
        cr = np.cross(d, light)
        assert np.allclose(cr / np.linalg.norm(d, axis=1)[:, None], 0, atol=2e-3)


def test_world_stubs():
    import a64 as A
    at = 0x99B000
    fn = {'sh_field': 0x99C000, 'sh_world': 0x99C100, 'sh_wcam': 0x99C200}
    w = SH.world_stub(at, 0x4C1F350, fn)
    bls = [at + 4 * i + 4 * ((((x & 0x3FFFFFF) ^ 0x2000000) - 0x2000000))
           for i, x in enumerate(w) if x >> 26 == 0x25]
    assert bls == [SH.GUEST_TO_HOST, 0x99C100, SH.MODEL_TARGET, SH.MODEL_TARGET]
    w = SH.wcam_stub(at, 0x4C1F350, fn)
    bls = [at + 4 * i + 4 * ((((x & 0x3FFFFFF) ^ 0x2000000) - 0x2000000))
           for i, x in enumerate(w) if x >> 26 == 0x25]
    assert bls == [SH.WCAM_TARGET, 0x99C200] and w[-1] == A.ret()



def test_world_round_shadow_skipped_for_ground_models_only():
    m = Machine()
    for a in (PT, X86C, WPAGE, OPAGE):
        try:
            m.uc.mem_map(a, 0x10000)
        except Exception:
            pass
    q = lambda a, v: m.uc.mem_write(a, struct.pack('<Q', v))   # noqa: E731
    esp = 0x00140100
    q(PT + 8 * (esp >> 12), WPAGE)
    q(PT + 8 * (WOBJ >> 12), OPAGE)
    m.uc.mem_write(X86C + 0x10, struct.pack('<I', esp))
    m.uc.mem_write(WPAGE + (esp & 0xFFF) + 4, struct.pack('<I', WOBJ))
    for mid, skip in ((0, 1), (19, 1), (6, 1), (3, 0), (11, 0), (10, 0)):
        m.uc.mem_write(OPAGE + (WOBJ & 0xFFF) + 0x50, struct.pack('<B', mid))
        assert m.call('sh_wblob', CTX, X86C, PT) == skip, mid


def test_wblob_stub_calls_the_sprite_only_when_told():
    import a64 as A
    at = 0x99B000
    w = SH.wblob_stub(at, 0x4C1F350, {'sh_wblob': 0x99C300})
    bls = [at + 4 * i + 4 * ((((x & 0x3FFFFFF) ^ 0x2000000) - 0x2000000))
           for i, x in enumerate(w) if x >> 26 == 0x25]
    assert bls == [0x99C300, SH.WBLOB_TARGET]
    assert A.add_imm(10, 10, 4) in w and A.str_(10, 9, SH.X86_ESP) in w   # 647c: pop the slot


def test_world_shadow_only_pass_draws_shadows_and_skips_the_rest():
    m = Machine()
    m.u32(CTX, SH.MODE_ACTOR)
    m.u32(CTX + SH.CTX_SONLY, 1)
    m.u32(CTX + 4, 1)
    m.mat(RS + 0x28, np.eye(4))
    assert m.call('sh_pre', CTX, RS, 300) == 2           # shadowed part: its shadow
    assert m.call('sh_pre', CTX, RS, 6) == 1             # tiny part: nothing at all
    m.u32(CTX + 4, 0)
    assert m.call('sh_pre', CTX, RS, 300) == 1           # no floor: nothing
    m.u32(CTX + SH.CTX_SONLY, 0)
    assert m.call('sh_pre', CTX, RS, 300) == 0           # pass 2: the model itself


def test_field_small_raised_floor_keeps_its_shadow():
    """659: a model on a two-triangle walkmesh floor 160 above the rest
    (convil_1's item shop) keeps its shadow -- the 651 ledge rule is gone."""
    cam = _camera()
    ground = [((-2000, -2000, 0), (2000, -2000, 0), (-2000, 2000, 0)),
              ((2000, -2000, 0), (2000, 2000, 0), (-2000, 2000, 0)),
              ((-2000, 2000, 0), (2000, 2000, 0), (0, 3000, 0))]
    lz = 160
    ledge = [((2100, 0, lz), (2300, 0, lz), (2100, 200, lz)),
             ((2300, 0, lz), (2300, 200, lz), (2100, 200, lz))]
    acc = {0: (1,), 1: (0, 2), 2: (1,), 3: (4,), 4: (3,)}
    m = Machine()
    origin = np.array([2150.0, 50.0, lz - 10.0])
    pages = _field_world(m, ground + ledge, cam, origin, access=acc)
    _field_draw(m, [_part(cam, origin + (0, 0, 80))], pages)
    assert m.u32(CTX + 4) != 0


def test_del2_window_lady_casts_no_shadow():
    """659: del2 (field 443), a model the game keeps on triangle 120."""
    cam = _camera()
    tris = [((0, 0, 0), (1000, 0, 0), (0, 1000, 0))] * 121
    for fid, shadow in ((443, False), (444, True)):
        m = Machine()
        origin = np.array([100.0, 100.0, -10.0])
        pages = _field_world(m, tris, cam, origin)
        tab, ev = LEVEL + 0x3000, LEVEL + 0x3800
        g = lambda a: LVL_PAGE + (a - LEVEL)                      # noqa: E731
        m.uc.mem_write(CAM_PAGE + 0x738, struct.pack('<I', tab))
        m.uc.mem_write(CAM_PAGE + 0x73E, struct.pack('<H', 1))
        m.uc.mem_write(CAM_PAGE + 0x468, struct.pack('<H', fid))
        m.uc.mem_write(g(tab + 0x178), struct.pack('<I', S110 - 0x38))
        m.uc.mem_map(0x330000, 0x10000)
        m.uc.mem_write(pages + 8 * 0xCC0, struct.pack('<Q', 0x330000))
        m.uc.mem_write(0x330000 + 0xB60, struct.pack('<I', ev))
        m.uc.mem_write(g(ev + 0x78), struct.pack('<h', 120))
        _field_draw(m, [_part(cam, origin + (0, 0, 80))], pages)
        assert (m.u32(CTX + 4) != 0) == shadow, fid


def test_lifestream_casts_no_shadow():
    """669: life (723) and life2 (724) -- models floating in a void over an
    invisible walkmesh cast no shadow; the fields around them still do."""
    cam = _camera()
    tris = [((0, 0, 0), (1000, 0, 0), (0, 1000, 0))] * 121
    for fid, shadow in ((722, True), (723, False), (724, False), (725, True)):
        m = Machine()
        origin = np.array([100.0, 100.0, -10.0])
        pages = _field_world(m, tris, cam, origin)
        tab, ev = LEVEL + 0x3000, LEVEL + 0x3800
        g = lambda a: LVL_PAGE + (a - LEVEL)                      # noqa: E731
        m.uc.mem_write(CAM_PAGE + 0x738, struct.pack('<I', tab))
        m.uc.mem_write(CAM_PAGE + 0x73E, struct.pack('<H', 1))
        m.uc.mem_write(CAM_PAGE + 0x468, struct.pack('<H', fid))
        m.uc.mem_write(g(tab + 0x178), struct.pack('<I', S110 - 0x38))
        m.uc.mem_map(0x330000, 0x10000)
        m.uc.mem_write(pages + 8 * 0xCC0, struct.pack('<Q', 0x330000))
        m.uc.mem_write(0x330000 + 0xB60, struct.pack('<I', ev))
        m.uc.mem_write(g(ev + 0x78), struct.pack('<h', 5))
        _field_draw(m, [_part(cam, origin + (0, 0, 80))], pages)
        assert (m.u32(CTX + 4) != 0) == shadow, fid


def test_field_shadow_fades_with_height_like_ffnx():
    """655: FFNx's field fade -- full up to 100 above the model's floor, gone
    100 later; hips height alone does not matter."""
    cam = _camera()
    ground = [((-2000, -2000, 0), (2000, -2000, 0), (-2000, 2000, 0)),
              ((2000, -2000, 0), (2000, 2000, 0), (-2000, 2000, 0)),
              ((-2000, 2000, 0), (2000, 2000, 0), (0, 3000, 0))]
    acc = {0: (1,), 1: (0, 2), 2: (1,)}
    for oz, hips, shadow, fade in ((-10.0, 80.0, True, 0), (-10.0, 200.0, True, 0),
                                   (160.0, 80.0, True, 71), (240.0, 80.0, False, None)):
        m = Machine()
        origin = np.array([100.0, 100.0, oz])
        pages = _field_world(m, ground, cam, origin, access=acc)
        _field_draw(m, [_part(cam, origin + (0, 0, hips))], pages)
        assert (m.u32(CTX + 4) != 0) == shadow, (oz, hips)
        if fade is not None:
            assert abs(m.u32(CTX + SH.CTX_FADE) - fade) <= 1, (oz, m.u32(CTX + SH.CTX_FADE))


def test_field_floor_is_the_games_own_triangle():
    """655: two floors under one (x, y) -- the model's own walkmesh triangle
    (field_event_data[i].triangle) decides, not the nearest height."""
    cam = _camera()
    tri = lambda z: ((0, 0, z), (1000, 0, z), (0, 1000, z))      # noqa: E731
    for game, want in ((0, 289.0), (1, -11.0), (None, 289.0)):
        m = Machine()
        origin = np.array([100.0, 100.0, 290.0])
        pages = _field_world(m, [tri(300), tri(0)], cam, origin)
        if game is not None:
            tab, ev = LEVEL + 0x3000, LEVEL + 0x3800
            g = lambda a: LVL_PAGE + (a - LEVEL)                  # noqa: E731
            m.uc.mem_write(CAM_PAGE + 0x738, struct.pack('<I', tab))
            m.uc.mem_write(CAM_PAGE + 0x73E, struct.pack('<H', 1))
            m.uc.mem_write(g(tab + 0x178), struct.pack('<I', S110 - 0x38))
            m.uc.mem_map(0x330000, 0x10000)
            m.uc.mem_write(pages + 8 * 0xCC0, struct.pack('<Q', 0x330000))
            m.uc.mem_write(0x330000 + 0xB60, struct.pack('<I', ev))
            m.uc.mem_write(g(ev + 0x78), struct.pack('<h', game))
        got = _field_draw(m, [_part(cam, origin + (0, 0, 80))], pages)
        if want == -11.0:
            # far below its own floor plane: 290 up -> faded out entirely
            assert m.u32(CTX + 4) == 0
        else:
            assert abs(_landed(cam, got[1], np.zeros((1, 3)))[0][2] - want) < 1e-2, game


def test_field_shadow_stays_under_a_raised_model():
    """657: climbing (model 150 above its floor) keeps the shadow under the
    model instead of sliding it along the light's slant."""
    cam = _camera()
    ground = [((-2000, -2000, 0), (2000, -2000, 0), (-2000, 2000, 0)),
              ((2000, -2000, 0), (2000, 2000, 0), (-2000, 2000, 0)),
              ((-2000, 2000, 0), (2000, 2000, 0), (0, 3000, 0))]
    acc = {0: (1,), 1: (0, 2), 2: (1,)}
    spots = []
    for oz in (-10.0, 150.0):
        m = Machine()
        origin = np.array([100.0, 100.0, oz])
        pages = _field_world(m, ground, cam, origin, access=acc)
        got = _field_draw(m, [_part(cam, origin + (0, 0, 80))], pages)
        spots.append(_landed(cam, got[1], np.zeros((1, 3)))[0])
    assert np.allclose(spots[0][:2], spots[1][:2], atol=1.5), spots


def test_field_model_below_its_floor_casts_no_shadow():
    """660: climbing down below the floor the game still says it is on, a
    model casts nothing (it used to shade the floor ABOVE it)."""
    cam = _camera()
    tri = lambda z: ((0, 0, z), (1000, 0, z), (0, 1000, z))      # noqa: E731
    for oz, shadow in ((290.0, True), (150.0, False)):
        m = Machine()
        origin = np.array([100.0, 100.0, oz])
        pages = _field_world(m, [tri(300)], cam, origin)
        tab, ev = LEVEL + 0x3000, LEVEL + 0x3800
        g = lambda a: LVL_PAGE + (a - LEVEL)                      # noqa: E731
        m.uc.mem_write(CAM_PAGE + 0x738, struct.pack('<I', tab))
        m.uc.mem_write(CAM_PAGE + 0x73E, struct.pack('<H', 1))
        m.uc.mem_write(g(tab + 0x178), struct.pack('<I', S110 - 0x38))
        m.uc.mem_map(0x330000, 0x10000)
        m.uc.mem_write(pages + 8 * 0xCC0, struct.pack('<Q', 0x330000))
        m.uc.mem_write(0x330000 + 0xB60, struct.pack('<I', ev))
        m.uc.mem_write(g(ev + 0x78), struct.pack('<h', 0))
        _field_draw(m, [_part(cam, origin + (0, 0, 80))], pages)
        assert (m.u32(CTX + 4) != 0) == shadow, oz


# ------------------------------------------------------------ BUILD 662
def _game_tri(m, pages, t):
    tab, ev = LEVEL + 0x3000, LEVEL + 0x3800
    g = lambda a: LVL_PAGE + (a - LEVEL)                          # noqa: E731
    m.uc.mem_write(CAM_PAGE + 0x738, struct.pack('<I', tab))
    m.uc.mem_write(CAM_PAGE + 0x73E, struct.pack('<H', 1))
    m.uc.mem_write(g(tab + 0x178), struct.pack('<I', S110 - 0x38))
    m.uc.mem_map(0x330000, 0x10000)
    m.uc.mem_write(pages + 8 * 0xCC0, struct.pack('<Q', 0x330000))
    m.uc.mem_write(0x330000 + 0xB60, struct.pack('<I', ev))
    m.uc.mem_write(g(ev + 0x78), struct.pack('<h', t))


def test_field_sinking_into_a_ladder_hole_fades_the_shadow_out():
    """662: going down a ladder hole the shadow fades over 20 units below
    standing height (660 cut it at 16 below, at full strength)."""
    cam = _camera()
    tri = lambda z: ((0, 0, z), (1000, 0, z), (0, 1000, z))      # noqa: E731
    for oz, fade in ((290.0, 0), (285.0, 20), (280.0, 45), (271.0, 90),
                     (269.5, 97), (268.0, None), (150.0, None)):
        m = Machine()
        origin = np.array([100.0, 100.0, oz])
        pages = _field_world(m, [tri(300)], cam, origin)
        _game_tri(m, pages, 0)
        _field_draw(m, [_part(cam, origin + (0, 0, 80))], pages)
        assert (m.u32(CTX + 4) != 0) == (fade is not None), oz
        if fade is not None:
            assert abs(m.u32(CTX + SH.CTX_FADE) - fade) <= 1, (
                oz, m.u32(CTX + SH.CTX_FADE))


LADDER = [((0, 0, 600), (1000, 0, 600), (0, 1000, 600)),        # 0 top floor
          ((2000, 2000, 170), (2060, 2000, 170), (2000, 2060, 170)),   # 1-2:
          ((2060, 2000, 170), (2060, 2060, 170), (2000, 2060, 170)),   # rung island
          ((1500, 1500, 0), (2600, 1500, 0), (1500, 2600, 0)),  # 3-4 bottom
          ((2600, 1500, 0), (2600, 2600, 0), (1500, 2600, 0))]
LADDER_ACC = {1: (2,), 2: (1,), 3: (4,), 4: (3,)}


def test_field_model_off_its_floor_skips_ladder_scaffolding():
    """662, convil_1's long ladder: a model the game keeps on the top floor
    but now hanging over a 2-triangle rung island (60 across) is shadowed
    on the real floor below it, never on the island; an untracked model
    keeps the old nearest-level search."""
    cam = _camera()
    for tracked, want in ((True, -11.0), (False, 159.0)):
        m = Machine()
        origin = np.array([2030.0, 2030.0, 150.0])
        pages = _field_world(m, LADDER, cam, origin, access=LADDER_ACC)
        if tracked:
            _game_tri(m, pages, 0)
        got = _field_draw(m, [_part(cam, origin + (0, 0, 80))], pages)
        assert got[0] == 2, tracked
        assert abs(_landed(cam, got[1], np.zeros((1, 3)))[0][2] - want) < 1e-2
    # a big 2-triangle floor (the item shop's) is not scaffolding
    big = [LADDER[0], ((2000, 2000, 170), (2400, 2000, 170), (2000, 2400, 170)),
           ((2400, 2000, 170), (2400, 2400, 170), (2000, 2400, 170))]
    m = Machine()
    origin = np.array([2030.0, 2030.0, 190.0])
    pages = _field_world(m, big, cam, origin, access={1: (2,), 2: (1,)})
    _game_tri(m, pages, 0)
    got = _field_draw(m, [_part(cam, origin + (0, 0, 80))], pages)
    assert abs(_landed(cam, got[1], np.zeros((1, 3)))[0][2] - 159.0) < 1e-2


def test_field_model_off_its_floor_over_nothing_casts_no_shadow():
    """662: mid-ladder over a void, the game still naming its floor: no
    shadow (it used to be drawn at the model's own feet, in mid-air)."""
    cam = _camera()
    m = Machine()
    origin = np.array([5000.0, 5000.0, 300.0])
    pages = _field_world(m, LADDER, cam, origin, access=LADDER_ACC)
    _game_tri(m, pages, 0)
    _field_draw(m, [_part(cam, origin + (0, 0, 80))], pages)
    assert m.u32(CTX + 4) == 0


def test_convil_1_ladder_rungs_are_scaffolding_and_its_floors_are_not():
    """The real walkmesh: the three islands on the long ladder (z 337, 153,
    -30) are scaffolding by the 662 rule; every LADER destination floor and
    the item shop's 2-triangle pieces are not."""
    import importlib.util
    import os
    p = os.path.join(os.path.dirname(__file__), '..', 'attic', 'dirs', '_scratch', '661', 'wm.py')
    if not os.path.exists(p):
        pytest.skip('no walkmesh reader')
    spec = importlib.util.spec_from_file_location('wm662', p)
    W = importlib.util.module_from_spec(spec)
    cwd = os.getcwd()
    os.chdir(os.path.join(os.path.dirname(__file__), '..'))
    try:
        spec.loader.exec_module(W)
        w = W.wm('convil_1')
    finally:
        os.chdir(cwd)
    n = len(w)

    def island(t):
        nb = {a for a in w.access_pool[t] if 0 <= a < n and a != t}
        if not nb:
            return True
        if len(nb) > 1:
            return False
        u = nb.pop()
        return not {a for a in w.access_pool[u] if 0 <= a < n and a not in (t, u)}

    def scaffold(t):
        v = w.sector_pool[t]
        for k in (0, 1):
            if max(p[k] for p in v) - min(p[k] for p in v) >= 128:
                return False
        return island(t)
    rungs = [t for t in range(n) if scaffold(t)
             and -13 <= min(p[0] for p in w.sector_pool[t]) <= 62
             and {p[2] for p in w.sector_pool[t]} & {337, 153, -30, -29}]
    assert len(rungs) >= 3
    # LADER ends on real floors. (143/153, the well ladder's ends, 176,
    # 26 at the item-shop/side ladders' tops are 2-triangle landings: they
    # only matter to a model ABOVE them while off its floor, i.e. the last
    # moment of a climb down onto them -- that shadow appears on landing.)
    for t in (171, 74, 89, 86, 22):
        assert not scaffold(t), t
    for t in range(n):                                          # z 1125 shop
        if all(p[2] == 1125 for p in w.sector_pool[t]):
            assert not scaffold(t), t


# ------------------------------------------------------------ BUILD 663
FOOT = [((0, 0, 600), (1000, 0, 600), (0, 1000, 600)),          # 0 top floor
        ((1500, 1500, 0), (2600, 1500, 0), (1500, 2600, 0)),    # 1-2 bottom
        ((2600, 1500, 0), (2600, 2600, 0), (1500, 2600, 0))]
FOOT_ACC = {1: (2,), 2: (1,)}


def test_field_ladder_foot_just_past_the_floor_edge_fades_in():
    """663: convil_1's ladder foot hangs just past its floor's edge. A model
    off its floor within EDGE_NEAR of a floor below is shadowed on that
    floor's plane, fading in with the height (FFNx 100..200), instead of
    appearing only when it steps off the ladder."""
    cam = _camera()
    for oz, fade in ((40.0, 0), (150.0, 61), (185.0, 96), (195.0, None)):
        m = Machine()
        origin = np.array([1470.0, 1800.0, oz])      # 30 past the x = 1500 edge
        pages = _field_world(m, FOOT, cam, origin, access=FOOT_ACC)
        _game_tri(m, pages, 0)                       # the game: still on top
        got = _field_draw(m, [_part(cam, origin + (0, 0, 80))], pages)
        assert (m.u32(CTX + 4) != 0) == (fade is not None), oz
        if fade is not None:
            assert abs(m.u32(CTX + SH.CTX_FADE) - fade) <= 1, (oz, m.u32(CTX + SH.CTX_FADE))
            assert abs(_landed(cam, got[1], np.zeros((1, 3)))[0][2] + 11.0) < 1e-2
    # farther than EDGE_NEAR from any floor: nothing (the mid-air ladder)
    m = Machine()
    origin = np.array([1400.0, 1800.0, 100.0])
    pages = _field_world(m, FOOT, cam, origin, access=FOOT_ACC)
    _game_tri(m, pages, 0)
    _field_draw(m, [_part(cam, origin + (0, 0, 80))], pages)
    assert m.u32(CTX + 4) == 0


def test_field_off_its_floor_never_picks_a_floor_above():
    """663: climbing UP off the bottom floor, a floor overhead that holds
    (x, y) used to win the nearest-height search and the shadow vanished at
    once; only floors below the model receive it, so it fades out."""
    cam = _camera()
    tris = [((1500, 1500, 0), (2600, 1500, 0), (1500, 2600, 0)),  # 0 bottom
            ((1000, 1000, 330), (3000, 1000, 330), (1000, 3000, 330))]  # 1 above
    for oz, fade in ((60.0, 0), (160.0, 71)):
        m = Machine()
        origin = np.array([1470.0, 1800.0, oz])     # off the bottom, under 1
        pages = _field_world(m, tris, cam, origin)
        _game_tri(m, pages, 0)
        got = _field_draw(m, [_part(cam, origin + (0, 0, 80))], pages)
        assert m.u32(CTX + 4) != 0, oz
        assert abs(m.u32(CTX + SH.CTX_FADE) - fade) <= 1, (oz, m.u32(CTX + SH.CTX_FADE))
        assert abs(_landed(cam, got[1], np.zeros((1, 3)))[0][2] + 11.0) < 1e-2


def test_667_alloc_stub_notes_pass1_entries_and_two_pass_marks_them():
    """BUILD 667: an entry queued in a shadow-only pass is noted (up to
    DENT_MAX) and marked DE_SKIP after pass 1; outside it nothing is noted."""
    from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
    from unicorn import arm64_const as R
    import a64 as A
    at, state = 0x10000, 0x20000
    words = SH.alloc_stub(at, state)
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    uc.mem_map(0, 0x40000)
    uc.mem_write(at, b''.join(struct.pack('<I', x) for x in words))
    ret = at + 4 * len(words) + 0x100
    uc.mem_write(ret, struct.pack('<I', 0xD503201F))            # nop

    def run(entry, mode=SH.MODE_ACTOR, sonly=1):
        uc.mem_write(state, struct.pack('<I', mode))
        uc.mem_write(state + SH.CTX_SONLY, struct.pack('<I', sonly))
        uc.reg_write(R.UC_ARM64_REG_X0, entry)
        uc.reg_write(R.UC_ARM64_REG_X30, ret)
        uc.emu_start(at, ret)
        assert uc.reg_read(R.UC_ARM64_REG_X20) == entry
    u32 = lambda a: struct.unpack('<I', uc.mem_read(a, 4))[0]   # noqa: E731
    u64 = lambda a: struct.unpack('<Q', uc.mem_read(a, 8))[0]   # noqa: E731
    run(0x30000, sonly=0)
    assert u32(state + SH.CTX_NDEF) == 0
    for i in range(SH.DENT_MAX + 2):
        run(0x30000 + 0x100 * i)
    assert u32(state + SH.CTX_NDEF) == SH.DENT_MAX
    assert [u64(state + SH.CTX_DENT + 8 * i) for i in range(SH.DENT_MAX)] == \
        [0x30000 + 0x100 * i for i in range(SH.DENT_MAX)]
    run(0x38000, mode=SH.MODE_BLOB, sonly=0)
    assert u64(state + SH.CTX_ENTRY) == 0x38000
    assert SH.CTX_DENT + 8 * SH.DENT_MAX <= SH.STATE_BYTES
    w = SH.world_stub(0x99B000, state, {'sh_world': 0x99C000})
    assert A.movz(12, SH.DE_SKIP) in w and A.str_(31, 9, SH.CTX_NDEF) in w
    src = open(os.path.join(HERE, 'native', 'shadows.c')).read()
    assert '== 0x%X, "CTX_NDEF"' % SH.CTX_NDEF in src and '== 0x%X, "CTX_DENT"' % SH.CTX_DENT in src


def test_667d_field_parts_are_marked_and_unmarked():
    """An opaque (mode 4) part of a shadowed field model is drawn with
    blendMode.x = MARK_BIT | fade << 8 | 4; the next helper call restores
    the cached mode first; a non-opaque part is never marked; battle actors
    are never marked."""
    cam = _camera()
    m = Machine()
    origin = np.array([500.0, 300.0, 140.0])
    pages = _field_world(m, SLOPE, cam, origin)
    m.call('sh_field', CTX, S110, pages)
    m.u32(CTX, SH.MODE_ACTOR)
    m.mat(RS + 0x28, _part(cam, origin + (0, 0, 80)))
    m.u32(RS + SH.RS_BLEND_OFF, 4)
    assert m.call('sh_pre', CTX, RS) == 2
    fade = m.u32(CTX + SH.CTX_FADE)
    assert m.u32(RS + SH.RS_BLEND_OFF) == SH.MARK_BIT | (fade << 8) | 4
    assert m.u32(CTX + SH.CTX_MARKED) == 1 and m.u32(CTX + SH.CTX_MORIG) == 4
    # sh_begin sees the real mode (the slow path restores it in sh_end)
    m.call('sh_begin', CTX, RS, RB)
    m.call('sh_end', CTX, RS, RB)
    assert m.u32(RS + SH.RS_BLEND_OFF) == 4
    # a semi-transparent part: unmarked, the earlier mark gone
    m.mat(RS + 0x28, _part(cam, origin + (0, 0, 60)))
    m.u32(RS + SH.RS_BLEND_OFF, SH.MARK_BIT | 4)        # as the fast path leaves it
    m.u32(CTX + SH.CTX_MARKED, 1)
    m.u32(CTX + SH.CTX_MORIG, 1)
    assert m.call('sh_pre', CTX, RS) == 2
    assert m.u32(RS + SH.RS_BLEND_OFF) == 1 and m.u32(CTX + SH.CTX_MARKED) == 0
    # battle: never marked
    m2 = Machine()
    m2.mat(RS + 0x28, np.eye(4))
    m2.u32(CTX, SH.MODE_BLOB)
    m2.call('sh_pre', CTX, RS)
    m2.u32(CTX, SH.MODE_ACTOR)
    m2.u32(RS + SH.RS_BLEND_OFF, 4)
    m2.call('sh_pre', CTX, RS, 36)
    assert m2.u32(RS + SH.RS_BLEND_OFF) == 4


def test_667d_field_stub_takes_the_last_mark_off():
    import a64 as A
    at = 0x99B000
    w = SH.field_stub(at, 0x4C1F350, {'sh_field': 0x99C000})
    assert A.ldr(10, 9, SH.CTX_MORIG) in w and A.str_(10, 11, SH.RS_BLEND_OFF) in w
    assert A.str_(31, 9, SH.CTX_MARKED) in w


def test_667d_shader_mark_line():
    assert 'ff7nx_shadows 667d' in SH.SHADER_LINE
    assert 'if (blendMode.x >= %d)' % SH.MARK_BIT in SH.SHADER_LINE
    src = open(os.path.join(HERE, 'native', 'shadows.c')).read()
    assert '"CTX_MARKED"' in src and '0x%X, "CTX_MORIG"' % SH.CTX_MORIG in src
    assert 'MARK_BIT    0x%Xu' % SH.MARK_BIT in src


def test_667e_raised_model_raises_its_floor():
    """667e: the game raises some models per field (0xCFF8E8 + 4i, added to
    the model's z in x86 0x6392BB: mds7pb_1 Cloud +9). The shadow's floor
    goes up with the model: still 1 under its origin, no extra gap/fade.
    Unraised (0) and absurd values are left alone."""
    cam = _camera()
    tris = [((-2000, -2000, 0), (2000, -2000, 0), (-2000, 2000, 0))]
    for zo, want in ((0, -11.0), (9, -2.0), (-5, -16.0), (500, -11.0)):
        m = Machine()
        origin = np.array([-100.0, -100.0, -10.0 + (zo if abs(zo) < 64 else 0)])
        pages = _field_world(m, tris, cam, origin)
        tab, ev = LEVEL + 0x3000, LEVEL + 0x3800
        g = lambda a: LVL_PAGE + (a - LEVEL)                      # noqa: E731
        m.uc.mem_write(CAM_PAGE + 0x738, struct.pack('<I', tab))
        m.uc.mem_write(CAM_PAGE + 0x73E, struct.pack('<H', 1))
        m.uc.mem_write(CAM_PAGE + 0x468, struct.pack('<H', 154))
        m.uc.mem_write(CAM_PAGE + 0x8E8, struct.pack('<i', zo))   # model 0
        m.uc.mem_write(g(tab + 0x178), struct.pack('<I', S110 - 0x38))
        m.uc.mem_map(0x330000, 0x10000)
        m.uc.mem_write(pages + 8 * 0xCC0, struct.pack('<Q', 0x330000))
        m.uc.mem_write(0x330000 + 0xB60, struct.pack('<I', ev))
        m.uc.mem_write(g(ev + 0x78), struct.pack('<h', 0))
        got = _field_draw(m, [_part(cam, origin + (0, 0, 80))], pages)
        assert got[0] == 2, zo
        spot = _landed(cam, got[1], np.zeros((1, 3)))[0]
        assert abs(spot[2] - want) < 1e-2, (zo, spot)
        assert m.u32(CTX + SH.CTX_FADE) == 0


def test_670_fast_stub_hands_the_subcam_state_over():
    import a64 as A
    fn = {'sh_begin': 0x99C000}
    w = SH.fast_stub(0x99A000, 0x4C1F350, fn, 0x4C21180)
    i = w.index(A.str64(10, 0, SH.CTX_CAM))
    assert w[i - 2] == A.adrp(10, 0x99A000 + 4 * (i - 2), 0x4C21000)
    assert w[i - 1] == A.add_imm64(10, 10, 0x180)
    assert A.str64(31, 0, SH.CTX_CAM) in SH.fast_stub(0x99A000, 0x4C1F350, fn)


# ------------------------------------------------------------ BUILD 671
LIFT = [((-3, 338, -255), (-78, 288, -255), (-79, 338, -255)),     # 0-1 the
        ((-78, 288, -255), (-3, 338, -255), (-3, 288, -255)),      # shaft floor
        ((-3, 288, 25), (-78, 238, 25), (-79, 288, 25)),           # 2-3 the
        ((-78, 238, 25), (-3, 288, 25), (-3, 238, 25)),            # machine top
        ((-500, 230, 0), (500, 230, 0), (-500, -500, 0))]          # 4 bar floor
LIFT_ACC = {0: (1,), 1: (0,), 2: (3,), 3: (2,)}


def _lift_event(m, own, mtype, dest):
    _game_tri(m, PT, own)
    ev = LEVEL + 0x3800
    g = LVL_PAGE + (ev - LEVEL)
    m.uc.mem_write(g + 0x62, bytes((0, mtype)))
    m.uc.mem_write(g + 0x7A, struct.pack('<h', dest))


def test_671_lift_out_of_a_hidden_shaft_lands_on_its_top():
    """671, mds7pb_1: Barret rides the pinball machine up from triangle 0
    (z -255, a 2-triangle island under the bar) to triangle 2 (z 25) on a
    LADER (movement type 4, destination +0x7A). No shadow in the shaft or on
    the bar floor's plane over the hole; it fades in on the machine top over
    the last 20 units and is the same plane he then stands on."""
    cam = _camera()
    cases = [((-53.0, 320.0, -200.0), 4, 0, None, None),   # over the shaft floor
             ((-53.0, 300.0, -60.0), 4, 0, None, None),
             ((-53.0, 280.0, -20.0), 4, 0, None, None),    # under the top, deep
             ((-53.0, 276.0, 5.0), 4, 0, 45, 14.0),        # fading in
             ((-53.0, 273.0, 13.0), 4, 0, 5, 14.0),
             ((-53.0, 271.0, 15.0), 0, 2, 0, 14.0)]        # arrived: standing
    for origin, mtype, own, fade, plane in cases:
        m = Machine()
        origin = np.array(origin)
        pages = _field_world(m, LIFT, cam, origin, access=LIFT_ACC)
        _lift_event(m, own, mtype, 2)
        got = _field_draw(m, [_part(cam, origin + (0, 0, 80))], pages)
        assert (m.u32(CTX + 4) != 0) == (fade is not None), origin
        if fade is not None:
            assert abs(m.u32(CTX + SH.CTX_FADE) - fade) <= 1, (
                origin, m.u32(CTX + SH.CTX_FADE))
            assert abs(_landed(cam, got[1], np.zeros((1, 3)))[0][2] - plane) < 1e-2


def test_671_ladders_from_real_floors_are_unchanged():
    """The lift rule needs the named floor to be scaffolding: on a LADER
    from the big bar floor, or not on a LADER at all, the 662/663 search
    runs as before (here: the bar floor's plane, edge within 48)."""
    cam = _camera()
    origin = np.array([-53.0, 274.0, 10.0])
    for own, mtype in ((4, 4), (0, 0), (0, 1)):
        m = Machine()
        pages = _field_world(m, LIFT, cam, origin, access=LIFT_ACC)
        _lift_event(m, own, mtype, 2)
        got = _field_draw(m, [_part(cam, origin + (0, 0, 80))], pages)
        assert got[0] == 2, (own, mtype)
        assert abs(_landed(cam, got[1], np.zeros((1, 3)))[0][2] + 11.0) < 1e-2, (own, mtype)


# ------------------------------------------------------------ BUILD 673
PROP = [((5, -211, 0), (137, -133, 0), (48, -111, 0)),           # 0 named
        ((0, -300, 0), (236, -300, 0), (236, 100, 0)),            # 1 basement
        ((191, -65, 260), (191, -4, 260), (235, -65, 260)),       # 2-3 island
        ((191, -4, 260), (235, -4, 260), (235, -65, 260)),        # at z 260
        ((191, -65, 260), (150, -65, 260), (191, -4, 260))]       # 4: joined
PROP_ACC = {2: (3, 4), 3: (2,), 4: (2,)}       # (not scaffolding, as in mds7pb_2)


def _prop_event(m, own, mtype, dest, end, start=None, pos=None, state=None):
    """BUILD 766: a ride in progress has state (+0x70) 1 and its start
    (+0x18, copied when it began); `pos` is the model's own position (+0x0C,
    the entity's, 10 above the drawn origin)."""
    _lift_event(m, own, mtype, dest)
    g = LVL_PAGE + (LEVEL + 0x3800 - LEVEL)
    m.uc.mem_write(g + 0x7C, struct.pack('<3i', *(int(e) << 12 for e in end)))
    if start is not None:
        m.uc.mem_write(g + 0x18, struct.pack('<3i', *(int(e) << 12 for e in start)))
    if pos is not None:
        m.uc.mem_write(g + 0x0C, struct.pack('<3i', *(int(e) << 12 for e in pos)))
    if state is None:
        state = 1 if start is not None else 0
    m.uc.mem_write(g + 0x70, struct.pack('<h', state))


def test_673_prop_lift_never_shadows_on_an_island_above_its_travel():
    """673, mds7pb_2: the pinball machine rides a LADER at (254, -33), z 0
    <-> 340, destination triangle 3 (the z 260 island it never stands on:
    the end is outside its box). Near the top the 663 edge rule shadowed it
    on the island's plane, in mid-air; now only floors at the low end of its
    travel receive -- the basement floor while it is low, nothing up top."""
    cam = _camera()
    for oz, ok in ((40.0, True), (240.0, False), (300.0, False)):
        m = Machine()
        origin = np.array([254.0, -33.0, oz])
        pages = _field_world(m, PROP, cam, origin, access=PROP_ACC)
        _prop_event(m, 0, 4, 3, (254, -33, 340), start=(254, -33, 0),
                    pos=origin + (0, 0, 10))
        got = _field_draw(m, [_part(cam, origin + (0, 0, 80))], pages)
        assert (m.u32(CTX + 4) != 0) == ok, oz
        if ok:
            assert abs(_landed(cam, got[1], np.zeros((1, 3)))[0][2] + 11.0) < 1e-2
    # coming down from the top (named: the island) the same
    m = Machine()
    origin = np.array([254.0, -33.0, 300.0])
    pages = _field_world(m, PROP, cam, origin, access=PROP_ACC)
    _prop_event(m, 3, 4, 0, (254, -33, 0), start=(254, -33, 340),
                pos=origin + (0, 0, 10))
    _field_draw(m, [_part(cam, origin + (0, 0, 80))], pages)
    assert m.u32(CTX + 4) == 0


def test_673_a_climber_ending_on_its_floor_keeps_the_663_search():
    """The same place on a LADER that ENDS on its destination (inside its
    box), or not on a LADER at all: the island receives as before."""
    cam = _camera()
    origin = np.array([254.0, -33.0, 300.0])
    for mtype, end in ((4, (220, -30, 260)), (0, (254, -33, 340))):
        m = Machine()
        pages = _field_world(m, PROP, cam, origin, access=PROP_ACC)
        _prop_event(m, 0, mtype, 3, end)
        got = _field_draw(m, [_part(cam, origin + (0, 0, 80))], pages)
        assert got[0] == 2, mtype
        assert abs(_landed(cam, got[1], np.zeros((1, 3)))[0][2] - 249.0) < 1e-2, mtype


# ------------------------------------------------------------ BUILD 766
# mds7pb_2's walkmesh (the 7th Heaven build, 10-20), cut down: the basement
# floor (to x 252; the machine rides at x 254, beside it) with triangle 62,
# and the z 260 island 52/53/61 (60 left out), no link between.
B766 = [((137, -133, 0), (5, -211, 0), (48, -111, 0)),           # 0 basement
        ((62, -52, 0), (169, 8, 0), (168, -73, 0)),               # 1 = 62
        ((-300, -300, 0), (252, -300, 0), (252, 300, 0)),         # 2 to x 252
        ((107, -66, 260), (109, -3, 260), (191, -4, 260)),        # 3 = 61
        ((191, -4, 260), (235, -4, 260), (235, -65, 260)),        # 4 = 53
        ((107, -66, 260), (191, -4, 260), (235, -65, 260))]       # 5 = 52
B766_ACC = {0: (2,), 1: (2,), 2: (0, 1), 3: (5,), 4: (5,), 5: (3, 4)}


def _b766(oz, x=254.0, y=-33.0, hips=60.0):
    cam = _camera()
    m = Machine()
    origin = np.array([x, y, oz])
    pages = _field_world(m, B766, cam, origin, access=B766_ACC)
    return m, cam, origin, pages


def _b766_draw(m, cam, origin, pages, hips=60.0):
    got = _field_draw(m, [_part(cam, origin + (0, 0, hips))], pages)
    if got[0] != 2:
        return None
    return _landed(cam, got[1], np.zeros((1, 3)))[0][2], m.u32(CTX + SH.CTX_FADE)


def test_766_prop_arrival_frame_casts_nothing_on_the_island():
    """766 (a), hardware 10-20: the machine's last ride step names the
    destination (the island, 53) with the ride still running (state 2):
    the 673 low end took the island's own height, and the island received
    the machine 80 above it for one frame. The start (z 0) keeps it low."""
    m, cam, origin, pages = _b766(330.0)
    _prop_event(m, 4, 4, 4, (254, -33, 340), start=(254, -33, 0),
                pos=(254, -33, 340), state=2)
    assert _b766_draw(m, cam, origin, pages) is None
    # the 673 rule without the start (the old blob's view) would have used
    # the island: the same frame with the start AT the top is shadowed there
    m, cam, origin, pages = _b766(330.0)
    _prop_event(m, 4, 4, 4, (254, -33, 340), start=(254, -33, 340),
                pos=(254, -33, 340), state=2)
    got = _b766_draw(m, cam, origin, pages)
    assert got is not None and abs(got[0] - 249.0) < 1e-2


def test_766_a_ride_that_starts_in_mid_air_shadows_only_its_low_end():
    """766 (b): Tifa is put at z 360 on triangle 61 (the z 260 island, 100
    under her) and lowered to z 35 on 62. Her shadow lay on the island's
    plane, in the air, from 360 down to 240, then none until she landed.
    Now: none near the island, the basement floor's with FFNx's height fade
    as she comes down, no pop at the landing."""
    for oz, want in ((350.0, None), (250.0, None), (195.0, None),
                     (140.0, 'fade'), (100.0, 'fade'), (60.0, 0), (25.0, 0)):
        m, cam, origin, pages = _b766(oz, 145.0, -32.0)
        _prop_event(m, 3, 4, 1, (145, -32, 35), start=(145, -32, 360),
                    pos=origin + (0, 0, 10))
        got = _b766_draw(m, cam, origin, pages)
        if want is None:
            assert got is None, oz
        else:
            assert got is not None and abs(got[0] + 11.0) < 1e-2, (oz, got)
            assert (got[1] > 0) == (want == 'fade'), (oz, got)


def test_766_a_climber_from_its_own_floor_is_untouched():
    """A ride that STARTS on its named floor (every ladder; Cloud riding up
    from the basement) keeps 663/673: going down from the island the island
    still fades the shadow out (sink fade), going up the basement fades it."""
    m, cam, origin, pages = _b766(245.0, 145.0, -32.0)        # 5 under 250
    _prop_event(m, 3, 4, 1, (145, -32, 35), start=(145, -32, 260),
                pos=origin + (0, 0, 10))
    got = _b766_draw(m, cam, origin, pages)
    assert got is not None and abs(got[0] - 249.0) < 1e-2 and got[1] > 0
    m, cam, origin, pages = _b766(90.0, 145.0, -32.0)
    _prop_event(m, 1, 4, 3, (145, -32, 260), start=(145, -32, 0),
                pos=origin + (0, 0, 10))
    got = _b766_draw(m, cam, origin, pages)
    assert got is not None and abs(got[0] + 11.0) < 1e-2


def test_766_placed_in_mid_air_casts_nothing():
    """766 (c): standing (movement type 0) with its own position more than
    HOVER (64) above the floor it would shade -- Tifa between her XYZI and
    her ride, the machine resting at the top beside the island: none. A
    model standing on its floor, or raised less (nmkin_2's av_j, 39), and
    one raised only by an OFST offset (not in its own position) keep it."""
    for x, y, own, pz, oz, ok in (
            (145.0, -32.0, 3, 360, 350.0, False),     # Tifa, XYZI 100 up
            (254.0, -33.0, 4, 340, 330.0, False),     # the machine, beside
            (145.0, -32.0, 3, 260, 250.0, True),      # standing on it
            (145.0, -32.0, 3, 299, 289.0, True),      # 39 up
            (145.0, -32.0, 3, 260, 330.0, True)):     # OFST: drawn 80 up
        m, cam, origin, pages = _b766(oz, x, y)
        _prop_event(m, own, 0, 0, (0, 0, 0), pos=(x, y, pz))
        assert (_b766_draw(m, cam, origin, pages) is not None) == ok, (pz, oz)


def test_766_one_reading_keeps_671():
    """ride_low took lift_dest in: the 671 lift out of a hidden shaft
    (scaffolding named, LADER) still lands on its destination, and a lift
    with no destination still casts nothing."""
    cam = _camera()
    origin = np.array([-53.0, 276.0, 5.0])
    for start in (None, (-53, 320, -255)):          # before / after it began
        m = Machine()
        pages = _field_world(m, LIFT, cam, origin, access=LIFT_ACC)
        _prop_event(m, 0, 4, 2, (-53, 270, 25), start=start, pos=origin + (0, 0, 10))
        got = _field_draw(m, [_part(cam, origin + (0, 0, 80))], pages)
        assert got[0] == 2, start
        assert abs(_landed(cam, got[1], np.zeros((1, 3)))[0][2] - 14.0) < 1e-2
    m = Machine()
    pages = _field_world(m, LIFT, cam, origin, access=LIFT_ACC)
    _lift_event(m, 0, 4, 0)
    _field_draw(m, [_part(cam, origin + (0, 0, 80))], pages)
    assert m.u32(CTX + 4) == 0
