"""BUILD 584: FFNx's spherical world -- shader maths, block flag, CPU words.

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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import numpy as np                                             # noqa: E402

import a64 as A                                                # noqa: E402
import arm64emu                                                # noqa: E402
import ff7nx_cave                                              # noqa: E402
import ff7nx_daynight as D                                     # noqa: E402
import ff7nx_tables                                            # noqa: E402
import ff7nx_worldsphere as WS                                 # noqa: E402
import nxmap                                                   # noqa: E402

MODULE = os.path.join(ROOT, 'dump', 'exefs', 'main')
if not os.path.exists(MODULE):
    MODULE = os.path.join(os.path.expanduser('~/mnt/7th_heaven_nx'), 'dump',
                          'exefs', 'main')
HOST_SP = 0x50000000
BLOCK = HOST_SP + D.BLOCK_BASE
R = 250000.0


# --------------------------------------------------------------- the shader
def _ffnx(v):
    x, y, z = v
    rp = -R
    d = math.hypot(x, z)
    e = math.exp(y / rp)
    a = d / rp
    y2 = rp * e * math.cos(a) - rp
    d2 = rp * e * math.sin(a)
    k = d2 / d if d > 1e-3 else e
    return np.array([x * k, y2, z * k])


A_DEPTH, C_DEPTH = 0.99, 32.0


def _shader(M, p):
    """lmain_vv.glsl wsDepth() since BUILD 591: depth from the true view
    distance, ndc z = A (1 - C / w); x, y, w untouched."""
    clip = np.append(p, 1.0) @ M
    if clip[3] > 2.0:
        clip = clip.copy()
        clip[2] = A_DEPTH * (clip[3] - C_DEPTH)
    return clip


def _tl_shader(rhw, clip_w):
    """tlmain_vv.glsl: the same depth from rhw (ndc); every 3D vertex since
    BUILD 721 (rhw == 1 is 2D)."""
    if 0.0 < rhw and rhw != 1.0:
        return A_DEPTH * (1.0 - C_DEPTH * rhw)
    return None


def _rot(yaw, pitch, roll):
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cr, sr = math.cos(roll), math.sin(roll)
    Ry = np.array([[cy, 0, -sy], [0, 1, 0], [sy, 0, cy]])
    Rx = np.array([[1, 0, 0], [0, cp, sp], [0, -sp, cp]])
    Rz = np.array([[cr, sr, 0], [-sr, cr, 0], [0, 0, 1]])
    return Ry @ Rx @ Rz


def test_shader_keeps_position_and_only_sets_depth():
    """BUILD 591: models are dropped on the CPU; the shader leaves x, y, w
    alone, keeps depth order, never reaches the far plane, and gives a model
    and a TL vertex (terrain, shadow) at the same distance the same depth."""
    rng = np.random.default_rng(591)
    for trial in range(200):
        Rv = _rot(rng.uniform(-3.1, 3.1), rng.uniform(0.05, 1.4),
                  rng.uniform(-0.2, 0.2)) @ np.diag([1, -1, 1])
        cam = rng.uniform(-50000, 50000, 3)
        WV = np.eye(4)
        WV[:3, :3] = Rv
        WV[3, :3] = -(cam @ Rv)
        zn, zf = rng.uniform(10, 100), rng.uniform(1e4, 1e6)
        P = np.array([[1.5, 0, 0, 0], [0, -1.5, 0, 0],
                      [0, 0, zf / (zf - zn), 1],
                      [0, 0, -zn * zf / (zf - zn), 0]])
        M = WV @ P
        for _ in range(10):
            p = cam + rng.uniform(-300000, 300000, 3)
            clip = np.append(p, 1.0) @ M
            if clip[3] <= 2:
                continue
            got = _shader(M, p)
            assert np.allclose(got[[0, 1, 3]], clip[[0, 1, 3]])
            nd = got[2] / got[3]
            assert nd < A_DEPTH                     # never far-clipped
            assert abs(nd - _tl_shader(1.0 / clip[3], clip[3])) < 1e-9


def test_depth_keeps_order_below_the_sky_backdrop():
    Zs = np.linspace(40.0, 2e6, 200000)
    nd = A_DEPTH * (1.0 - C_DEPTH / Zs)
    assert np.all(np.diff(nd) > 0)
    assert nd.max() < 0.998                          # the 0.999 backdrop
    assert _tl_shader(1.0, 1.0) is None              # 2D keeps its depth
    # 24-bit depth resolution at 35000 (the zoom limit) in world units
    Z = 35000.0
    assert (2.0 ** -24) * 2 * Z * Z / (A_DEPTH * C_DEPTH) < 10.0


def test_the_arc_lowers_far_terrain_and_ignores_turning():
    """Same distance, any heading: same drop (the stock sink's screen-x term
    is exactly what made land rise and sink as the camera turned)."""
    drops = []
    for yaw in np.linspace(-1, 1, 9):
        v = np.array([20480 * math.sin(yaw), 0.0, 20480 * math.cos(yaw)])
        drops.append(_ffnx(v)[1])
    assert max(drops) - min(drops) < 1e-6
    assert 830 < drops[0] < 845          # d^2 / 2R at 2.5 map meshes


def test_shipped_shader_carries_the_bend():
    text = open(os.path.join(ROOT, 'shaders', 'wide_screen',
                             'lmain_vv.glsl')).read()
    assert WS.SHADER_MARK in text
    assert text.index('gl_Position = wsDepth(gl_Position)') < text.index(
        'gl_Position.x *= WS_SCALE')
    tl = open(os.path.join(ROOT, 'shaders', 'wide_screen',
                           'tlmain_vv.glsl')).read()
    for t in (text, tl):
        assert '#define WS_DEPTH_A 0.99' in t and '#define WS_DEPTH_C 32.0' in t
    assert 'WS_DEPTH_A * (1.0 - WS_DEPTH_C * VertexCoord.w)' in tl
    assert D.SHADER_MARK in text         # the day/night tint is still there


# ------------------------------------------------------- the module words
_L = {}


def _layout():
    if 'l' not in _L:
        blob = open(MODULE, 'rb').read()
        segs, _raw, own = ff7nx_tables.open_module(blob)
        text = own.text
        WS.check_sites(text)
        bss = D.AC.scratch_base(blob, segs)
        pool = ff7nx_cave.HolePool(text,
                                   starts=set(nxmap.Main(MODULE).arm_starts))
        entries, placed, *_rest = D.build_all(
            pool, own, bss, bytes(D.BITMAP_BYTES), 40, world_sphere=True)
        _L['l'] = (entries, placed, bss)
    return _L['l']


def test_cpu_sink_and_model_call_are_replaced():
    entries, placed, _bss = _layout()
    assert placed[WS.SINK_SITE] == WS.SINK_NEW
    assert placed[WS.MODEL_CALL] == A.bl(WS.MODEL_CALL, entries['ws_model'])


def _block(mode):
    entries, placed, bss = _layout()
    hooks = {s for _n, s, _o, _w in D.all_sites()} | {WS.SINK_SITE,
                                                      WS.MODEL_CALL}
    code = {va: w for va, w in placed.items() if va not in hooks}
    mem = arm64emu.Mem()
    cpu = arm64emu.Cpu(mem, paged=True)
    cpu.sp = HOST_SP
    mem.setu(bss + D.BSS_MODE, mode, 4)
    mem.setu(bss + D.BSS_TINT_INV, 0, 4)
    for off in range(0, D.BLOCK_SIZE, 4):
        mem.setu(BLOCK + off, 0x11111111 + off, 4)
    assert cpu.run(entries['block'], code, max_steps=4000) == D.BLOCK_RESUME
    return {off: mem.u(BLOCK + off, 4) for off in range(0, D.BLOCK_SIZE, 4)}


def test_block_flag_only_in_world_mode():
    world = _block(D.DRIVER_WORLD)
    assert world[WS.WORLD_LANE] == WS.WORLD_MAGIC
    assert world[D.MAGIC_LANE] == D.TINT_MAGIC
    for mode in (D.DRIVER_FIELD, D.DRIVER_BATTLE, 0, 13):
        assert _block(mode)[WS.WORLD_LANE] == 0
    # blendMode.x and the matrix are never touched
    for off in [0] + list(range(0x10, D.BLOCK_SIZE, 4)):
        assert world[off] == 0x11111111 + off


def test_model_stub_returns_zero_and_pops_the_return_address():
    entries, placed, _bss = _layout()
    hooks = {s for _n, s, _o, _w in D.all_sites()} | {WS.SINK_SITE,
                                                      WS.MODEL_CALL}
    code = {va: w for va, w in placed.items() if va not in hooks}
    RET = 0x7000                          # outside the code map: stop
    mem = arm64emu.Mem()
    cpu = arm64emu.Cpu(mem)
    ctx = 0x60000000
    mem.setu(ctx, 0x12345678, 4)          # eax
    mem.setu(ctx + 0x10, 0x00100000, 4)   # esp at the call
    cpu.x[21] = ctx
    cpu.x[30] = RET
    assert cpu.run(entries['ws_model'], code, max_steps=100) == RET
    assert mem.u(ctx, 4) == 0
    assert mem.u(ctx + 0x10, 4) == 0x00100004


def test_switch():
    assert WS.enabled({})
    assert not WS.enabled({WS.ENV: '0'})


if __name__ == '__main__':
    for k, v in sorted(globals().items()):
        if k.startswith('test_'):
            v()
            print('ok', k)


def test_shader_carries_the_depth():
    text = open(os.path.join(ROOT, 'shaders', 'wide_screen',
                             'lmain_vv.glsl')).read()
    assert 'clip.z = WS_DEPTH_A * (clip.w - WS_DEPTH_C);' in text


def test_radius_is_ffnx_and_not_a_setting():
    assert WS.radius({WS.RADIUS_ENV: '60000'}) == 250000.0


def test_install_ships_both_vertex_shaders(tmp_path):
    """BUILD 591 regression: the radius rewrite deleted lmain_vv once the
    shader stopped carrying WS_SPHERE_RADIUS, which also switched the world
    sphere and far field off for the whole build."""
    import build
    import ff7nx_ws
    out = ff7nx_ws._install_shaders(str(tmp_path))
    card = os.path.join(str(tmp_path), 'atmosphere', 'contents',
                        build.TITLE_ID, 'romfs', 'ff7', 'shaders')
    assert sorted(os.path.basename(p) for p in out) == sorted(ff7nx_ws.WS_SHADERS)
    for n in ff7nx_ws.WS_SHADERS:
        assert os.path.exists(os.path.join(card, n))
    assert WS.shader_has_sphere(str(tmp_path), build.TITLE_ID)
