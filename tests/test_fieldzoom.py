"""BUILDs 567-569: per-field 4:3 zoom (ff7nx_fieldzoom).

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
import ff7nx_fieldzoom as Z  # noqa: E402
import nxmap  # noqa: E402

MAIN = os.path.join(HERE, 'dump', 'exefs', 'main')
FLEVEL = os.path.join(HERE, 'dump', 'romfs', 'ff7', 'workingdir', 'data',
                      'field', 'flevel.lgp')
SP = 0x7F000000
MTX = 0x6100000
VP = (0, 16, 640, 448)


def _f(v):
    return struct.unpack('<I', struct.pack('<f', v))[0]


def _b(u):
    return struct.unpack('<f', struct.pack('<I', u & 0xFFFFFFFF))[0]


def _patched(ids=(356,)):
    d = tempfile.mkdtemp()
    try:
        dest = os.path.join(d, 'main')
        info = Z.apply_to_nso(MAIN, dest, ids, stock=MAIN)
        return nxmap.Main(dest).text, info['bss']
    finally:
        shutil.rmtree(d)


def _entry(t, site):
    w = struct.unpack_from('<I', t, site)[0]
    assert w >> 26 == 0x05
    imm = w & 0x3FFFFFF
    return site + 4 * (imm - (1 << 26) if imm & (1 << 25) else imm)


def _code(t, entry, back):
    code, va = {}, entry
    while True:
        w = struct.unpack_from('<I', t, va)[0]
        code[va] = w
        if w >> 26 == 0x05:
            imm = w & 0x3FFFFFF
            if va + 4 * (imm - (1 << 26) if imm & (1 << 25) else imm) == back:
                return code
        va += 4


MODE_PTR = 0x6200000
MAT = [float(i + 1) for i in range(16)]


def _run(t1, bss, site, back, field=356, flag=0x5A5A, mode=2):
    mem = E.Mem()
    cpu = E.Cpu(mem, paged=True)
    g = cpu.guest_to_host
    mem.setu(g(Z.FIELD_ID), field, 2)
    mem.setu(bss, flag, 4)
    mem.setu(Z.MODE_SLOT, MODE_PTR, 8)
    mem.setu(MODE_PTR, mode, 4)
    for i, v in enumerate(MAT):
        mem.setu(SP + Z.MATRIX_SP + 4 * i, _f(v), 4)
    cpu.x[8] = 0x8888
    cpu.x[19], cpu.x[20], cpu.x[30] = 0x19, 0x20, 0xABC
    cpu.sp = SP
    out = cpu.run(_entry(t1, site), _code(t1, _entry(t1, site), back))
    assert out == back
    assert (cpu.x[19], cpu.x[20], cpu.x[30]) == (0x19, 0x20, 0xABC)
    assert cpu.sp == SP
    mat = [_b(mem.u(SP + Z.MATRIX_SP + 4 * i, 4)) for i in range(16)]
    return cpu, mem, mat


def test_on_arms_the_flag_only_for_the_listed_field():
    t1, bss = _patched()
    _c, mem, _m = _run(t1, bss, Z.ON_SITE, Z.ON_BACK, 356, flag=0)
    assert mem.u(bss, 4) == _f(4.0 / 3.0)
    for fid in (0, 355, 357, 116):
        _c, mem, _m = _run(t1, bss, Z.ON_SITE, Z.ON_BACK, fid, flag=7)
        assert mem.u(bss, 4) == 0


def test_off_disarms_and_keeps_w8():
    t1, bss = _patched()
    cpu, mem, _m = _run(t1, bss, Z.OFF_SITE, Z.OFF_BACK, flag=1)
    assert mem.u(bss, 4) == 0
    assert cpu.x[0] & 0xFFFFFFFF == 0x8888 + 8   # replayed add w0, w8, #8


def test_draw_scales_only_the_xy_rows_when_armed_in_field_mode():
    t1, bss = _patched()
    cpu, _mem, mat = _run(t1, bss, Z.DRAW_SITE, Z.DRAW_BACK,
                          flag=_f(4.0 / 3.0), mode=2)
    for i, v in enumerate(MAT):
        want = v * 4.0 / 3.0 if i in Z.XY_ROWS else v
        assert abs(mat[i] - _b(_f(want))) < 1e-5, (i, mat[i], want)
    assert cpu.x[24] == 0x12CE000                  # the displaced adrp


def test_draw_is_identity_unarmed_or_outside_the_field():
    t1, bss = _patched()
    z = _f(4.0 / 3.0)
    for flag, mode in ((0, 2), (z, 3), (z, 13), (z, 1), (0, 13)):
        cpu, _mem, mat = _run(t1, bss, Z.DRAW_SITE, Z.DRAW_BACK,
                              flag=flag, mode=mode)
        assert mat == MAT, (flag, mode)
        assert cpu.x[24] == 0x12CE000


def test_only_the_three_hooks_and_the_caves_change():
    import ff7nx_deadspace as DS
    t0 = nxmap.Main(MAIN).text
    t1, _bss = _patched()
    lo, hi = DS.part(MAIN, 'zoom')
    sites = (Z.ON_SITE, Z.OFF_SITE, Z.DRAW_SITE)
    ch = [va for va in range(0, len(t0), 4) if t0[va:va + 4] != t1[va:va + 4]]
    assert set(sites) <= set(ch)
    assert all(va in sites or lo <= va < hi for va in ch)


def test_bss_grows_by_the_flag_block():
    d = tempfile.mkdtemp()
    try:
        dest = os.path.join(d, 'main')
        Z.apply_to_nso(MAIN, dest, (356,), stock=MAIN)
        a = struct.unpack_from('<I', open(MAIN, 'rb').read(), 0x3C)[0]
        b = struct.unpack_from('<I', open(dest, 'rb').read(), 0x3C)[0]
        assert b == a + Z.BSS_BYTES
    finally:
        shutil.rmtree(d)


def test_maplist_and_env():
    if os.path.exists(FLEVEL):
        assert Z.maplist_index(FLEVEL, ['convil_2']) == {'convil_2': 356}
    assert Z.fields({}) == ('convil_2', 'ztruck')
    assert Z.fields({Z.ENV: 'a:1.5,b'}) == ('a', 'b')
    assert Z.factor('a', {Z.ENV: 'a:1.5,b'}) == 1.5
    assert Z.factor('b', {Z.ENV: 'a:1.5,b'}) == 4.0 / 3.0
    assert Z.fields({Z.ENV: '0'}) == ()


def test_618z11_per_field_factor_ztruck_350_units():
    """ztruck shows FFNx's 350-unit window: factor 426.67 / 350."""
    zt = Z.factor('ztruck', {})
    assert abs(zt - (320 / 0.75) / 350) < 1e-9
    t1, bss = _patched(((356, 4.0 / 3.0), (786, zt)))
    for fid, want in ((356, _f(4.0 / 3.0)), (786, _f(zt)), (785, 0)):
        _c, mem, _m = _run(t1, bss, Z.ON_SITE, Z.ON_BACK, fid, flag=7)
        assert mem.u(bss, 4) == want, (fid, mem.u(bss, 4))
    _c, _mem, mat = _run(t1, bss, Z.DRAW_SITE, Z.DRAW_BACK,
                         flag=_f(zt), mode=2)
    for i, v in enumerate(MAT):
        w = v * _b(_f(zt)) if i in Z.XY_ROWS else v
        assert abs(mat[i] - _b(_f(w))) < 1e-4, (i, mat[i], w)
