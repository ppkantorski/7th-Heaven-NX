"""BUILD 784: Chocobuckle's fat chocobo interpolated (FFNx's
ModelInterpolationEffectDecorator with usePauseTrick = false), and Shiva's /
Odin gunge's summon cameras decorated like the other 23.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'tests'))
import nxpaths  # noqa: E402
nxpaths.install()
import os  # noqa: E402

import pytest  # noqa: E402

import ff7nx_60fps as F  # noqa: E402
import ff7nx_dispatch as D  # noqa: E402
from ff7nx_dispatch_sites import DISPATCH_SITES, PAUSED_GUEST  # noqa: E402

CHOCOBUCKLE = 0x560C32


def _thr(monkeypatch):
    for k in (F.PAUSE_DEAF_ONE_CALL_ENV, F.MODELINTERP_ENV):
        monkeypatch.delenv(k, raising=False)
    d = DISPATCH_SITES['effect100']
    thr = dict(d['throttle'])
    thr['paused_guest'] = PAUSED_GUEST
    thr['idx_off'] = d['disp_hook']['idx_off']
    F._pause_deaf_one_call('effect100', thr, log=lambda *_: None)
    F._inject_model_interpolation('effect100', thr, log=lambda *_: None)
    return thr


def test_784_chocobuckle_moves_from_one_call_to_the_model_table(monkeypatch):
    thr = _thr(monkeypatch)
    oc = {va for _n, va in thr['one_call']}
    assert CHOCOBUCKLE not in oc
    assert 0x56149A in oc and 0x561737 in oc and 0x484A16 in oc
    model = {va: m for _n, va, m in thr['model']}
    assert model[CHOCOBUCKLE] == D.MODEL_NO_PAUSE | D.MODEL_HUGE
    nopause = {va for va, m in model.items() if m & D.MODEL_NO_PAUSE}
    assert {va for va, m in model.items() if m & D.MODEL_HUGE} == {CHOCOBUCKLE}
    assert nopause == {CHOCOBUCKLE, 0x5600BE, 0x56470F}
    assert not nopause & oc
    # every no-pause entry is a BUILD 783 pause-deaf routine
    assert nopause <= {va for _n, va in F.PAUSE_DEAF_ONE_CALL}


def test_784_model_off_leaves_chocobuckle_on_the_skip(monkeypatch):
    thr = _thr(monkeypatch)
    monkeypatch.setenv(F.MODELINTERP_ENV, '0')
    d = DISPATCH_SITES['effect100']
    t = dict(d['throttle'])
    F._pause_deaf_one_call('effect100', t, log=lambda *_: None)
    F._inject_model_interpolation('effect100', t, log=lambda *_: None)
    assert CHOCOBUCKLE in {va for _n, va in t['one_call']}
    assert not t.get('model')


def test_784_registration_and_pause_trick_paths_unchanged(monkeypatch):
    import test_throttle as T
    thr = _thr(monkeypatch)
    del T.FAIL[:]
    assert T.model_registration(thr, quiet=True) == len(thr['model'])
    assert T.model_interpolation(thr, quiet=True)
    assert T.one_call_balanced(thr, 4, quiet=True)
    assert not T.FAIL, T.FAIL


@pytest.mark.parametrize('extra,b,smooth', [
    (0, (30, -480, -7), True),
    (0, (1000, 0, 0), False),                   # 1000 is a teleport...
    (D.MODEL_HUGE, (1000, 0, 0), True),         # ...but not for Chocobuckle
    (D.MODEL_HUGE, (5000, -6000, 4000), True),
    (D.MODEL_HUGE, (10010, 0, 0), False),
])
def test_784_no_pause_marker_skips_and_interpolates(monkeypatch, extra, b,
                                                     smooth):
    import test_throttle as T
    E = T.E
    thr = _thr(monkeypatch)
    ctx = dict(reg=thr['ctx_reg'], base=T.GUEST_CTX, idx_off=thr['idx_off'],
               store_reg=thr['store_reg'])
    pre_base, post_base = T.CAVE, T.CAVE + 0x1000
    pre_site = T.make_site(ctx['reg'], ctx['idx_off'], ctx['store_reg'],
                           0x7D3050)
    post_site = dict(hook=thr['post'], displaced=T.A.mov_reg(0, 19))
    pre = D.build_throttle_pre_cave(pre_base, pre_site, thr, PAUSED_GUEST,
                                    thr['mask_bits'], 2)
    post = D.build_throttle_post_cave(post_base, post_site, thr)

    def put3(mem, at, p):
        for i, v in enumerate(p):
            mem.setu(at + 2 * i, v & 0xFFFF, 2)

    def get3(mem, at):
        return tuple(E.s16(mem.u(at + 2 * i, 2)) for i in range(3))

    a = (10, -400, 20)

    def q(v):
        return abs(v) // 4 if v >= 0 else -(abs(v) // 4)
    lerp = [tuple(x + q((y - x) * s) for x, y in zip(a, b)) for s in (1, 2, 3)]
    wants = ([a] + (lerp if smooth else [a, a, a]) + [b])

    idx = 13
    esp = 0x0012FFF8
    for was in (0, 1):
        mem = E.Mem()
        probe = E.Cpu(mem, host_base=T.HOST, paged=True)
        paused_host = probe.guest_to_host(PAUSED_GUEST)
        pos_host = probe.guest_to_host(thr['model_pos'])
        data_host = probe.guest_to_host(thr['data_base'] + idx * thr['stride'])
        mem.setu(thr['ctr'] + idx, 0x20 | D.MODEL_NO_PAUSE | extra, 1)
        mem.setu(paused_host, was, 1)
        mem.setu(data_host, 0x1234, 2)
        put3(mem, pos_host, (99, 99, 99))
        calls = 0
        for frame in range(9):
            cpu = T.new_cpu(mem, ctx, idx, CHOCOBUCKLE, esp)
            cpu.paged = True
            out = cpu.run(pre_base, pre)
            real = frame in (0, 1, 5)
            assert out == (pre_site['hook'] + 4 if real else thr['post']), \
                (frame, hex(out))
            assert cpu.x[0] & 0xFFFFFFFF == CHOCOBUCKLE, frame
            assert cpu.sp == 0x800000000, frame
            assert mem.u(T.GUEST_CTX + 0x10, 4) == (esp if real else esp + 4)
            # never paused: the routine ignores the flag, so it is never set
            assert mem.u(paused_host, 1) == was, frame
            if real:
                calls += 1
                put3(mem, pos_host, {0: a, 1: b, 5: b}[frame])
            cpu2 = E.Cpu(mem, host_base=T.HOST, paged=True)
            cpu2.sp = 0x800000000
            for r in range(31):
                cpu2.x[r] = E.GARBAGE
            cpu2.x[19] = 0x00BF23B8
            out = cpu2.run(post_base, post)
            assert out == post_site['hook'] + 4
            assert cpu2.x[0] & 0xFFFFFFFF == 0x00BF23B8
            assert mem.u(paused_host, 1) == was, frame
            if frame < 5:
                assert get3(mem, pos_host) == wants[frame], frame
            else:
                assert get3(mem, pos_host) == b, frame   # standing still
        assert calls == 3                                # 9 frames, 1 tick per 4


def test_784_shiva_and_odin_cameras_are_decorated(monkeypatch):
    monkeypatch.delenv(F.SHIVA_CAM_STEP_ENV, raising=False)
    monkeypatch.delenv(F.CAMERAHOLD_ENV, raising=False)
    thr = _thr(monkeypatch)
    thr['table'] = F.throttle_table('effect100', thr, [], log=lambda *_: None)
    F._inject_camera_hold('effect100', thr, log=lambda *_: None)
    got = {va: c for _n, va, c in thr['camera']}
    assert 0x58E60D in got and 0x4A0F52 in got
    assert got[0x58E60D] * D.CAM_THRESH_UNIT == 1500
    assert len(got) == len(D.EFFECT100_CAMERA)


def test_784_camera_switch(monkeypatch):
    monkeypatch.setenv(F.SHIVA_CAM_STEP_ENV, '1')
    monkeypatch.delenv(F.CAMERAHOLD_ENV, raising=False)
    thr = _thr(monkeypatch)
    thr['table'] = F.throttle_table('effect100', thr, [], log=lambda *_: None)
    F._inject_camera_hold('effect100', thr, log=lambda *_: None)
    got = {va for _n, va, _c in thr['camera']}
    assert 0x58E60D not in got and 0x4A0F52 not in got


EXE = os.path.join(nxpaths.ROOT, 'dump', 'romfs', 'ff7', 'resources',
                   'ff7_1.02', 'ff7_en')


@pytest.mark.skipif(not os.path.isfile(EXE), reason='no ff7_en')
def test_784_the_main_loop_writes_actor_3_position():
    capstone = pytest.importorskip('capstone')
    d = open(EXE, 'rb').read()
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    o = 0x56138A - 0x401000 + 0x200
    i = next(md.disasm(d[o:o + 16], 0x56138A))
    assert i.mnemonic == 'mov' and '0xbe63a2' in i.op_str
    assert 0xBE1178 + 3 * 0x1AEC + 0x166 == 0xBE63A2
    assert DISPATCH_SITES['effect100']['throttle']['model_pos'] == 0xBE63A2


SPOT = '/tmp/claude-0/p784/spot_1.p'


def test_784_light_widened_only_when_a_mod_owns_shiva(tmp_path, monkeypatch):
    import struct
    import build as B
    monkeypatch.delenv(B.SHIVA_LIGHT_ENV, raising=False)
    monkeypatch.delenv(B.SHIVA_LIGHT_OVERLAY_ENV, raising=False)
    monkeypatch.setattr(B, 'LIGHT_CACHE', str(tmp_path / 'lc'))
    # a stand-in fan: 3 vertices
    hdr = struct.pack('<16I', 1, 1, 1, 3, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 0, 1)
    body = struct.pack('<9f', -1, -20, 0, 0, -20, 0, 2.5, 0, 0) + b'\x80' * 12
    if os.path.isfile(SPOT):
        data = open(SPOT, 'rb').read()
    else:
        data = hdr.ljust(0x80, b'\0') + body
    van = tmp_path / 'spot_1.p'
    van.write_bytes(data)
    vd = {'spot_1.p': str(van)}
    log = []
    # no mod owns cyvadat: untouched
    m = {'cyvadat.p00': ('x', 'Nino')}
    assert B._widen_summon_lights('magic.lgp', m, vd, log.append) == m
    m = {'cyvadat.d': ('x', 'Nino'), 'cyvadat.p00': ('y', 'Nino')}
    out = B._widen_summon_lights('magic.lgp', m, vd, log.append)
    assert out['spot_1.p'][1] == 'Nino'
    new = open(out['spot_1.p'][0], 'rb').read()
    nv = struct.unpack_from('<I', data, 12)[0]
    for i in range(nv):
        a = struct.unpack_from('<3f', data, 0x80 + 12 * i)
        b = struct.unpack_from('<3f', new, 0x80 + 12 * i)
        assert abs(b[0] - a[0] * B.SHIVA_LIGHT_WIDTH) < 1e-4 and b[1:] == a[1:]
    # BUILD 785m: only the hundreds' depth bits change
    import p as P
    info = P.parse_header(data)
    if info:
        o = info['hundreds_offset']
        val, mask = struct.unpack_from('<II', new, o + 8)
        assert not val & 0x18000 and mask & 0x18000 == 0x18000
        diff = [i for i in range(len(data)) if data[i] != new[i]
                and i >= 0x80 + 12 * nv]
        assert all(o + 8 <= i < o + 16 for i in diff), diff
    # battle.lgp, a mod-shipped light, or width 1: untouched
    assert 'spot_1.p' not in B._widen_summon_lights('battle.lgp', m, vd,
                                                    log.append)
    mm = dict(m, **{'spot_1.p': ('z', 'Other')})
    assert B._widen_summon_lights('magic.lgp', mm, vd, log.append) == mm
    monkeypatch.setenv(B.SHIVA_LIGHT_ENV, '1')
    monkeypatch.setenv(B.SHIVA_LIGHT_OVERLAY_ENV, '0')
    assert B._widen_summon_lights('magic.lgp', m, vd, log.append) == m
