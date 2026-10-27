"""BUILD 582: animated FX requantised to Cosmos art; lost Cosmos detail.

Runs on the real mod, the vanilla dump and the shipped archive when present.

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

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'tests'))
PROJ = os.path.expanduser('~/mnt/7th_heaven_nx')
if PROJ not in sys.path:
    sys.path.append(PROJ)

import lgp  # noqa: E402
import field_bg_native as FN  # noqa: E402
import ff7nx_fxrequant as RQ  # noqa: E402
import ff7nx_lostdetail as LD  # noqa: E402
from test_fxsplit import _art, _field, _have  # noqa: E402

SHIPPED = os.path.join(PROJ, 'sdout', 'atmosphere', 'contents',
                       '0100A5B00BDC6000', 'romfs', 'ff7', 'workingdir',
                       'data', 'field', 'flevel.lgp')


def _shipped(name):
    with open(SHIPPED, 'rb') as h:
        h.read(12)
        n = struct.unpack('<i', h.read(4))[0]
        toc = [h.read(27) for _ in range(n)]
        for e in toc:
            if e[:20].split(b'\0')[0].decode().lower() == name:
                h.seek(struct.unpack('<I', e[20:24])[0])
                sz = struct.unpack('<I', h.read(24)[20:24])[0]
                return list(lgp.split_sections(
                    lgp.lzs_decompress(h.read(sz)[4:])))


def test_uniform_palettes_from_the_scripts():
    if not _have():
        return
    got = RQ.uniform_palettes(lgp.split_sections(_field('ghotel'))[0])
    assert got == {8: 255, 11: 255}, got
    got = RQ.uniform_palettes(lgp.split_sections(_field('jet'))[0])
    assert got == {7: 63, 8: 127, 9: 63, 12: 127, 13: 63}, got


def test_ghotel_fog_is_one_cosmos_picture_and_records_unchanged():
    if not (_have() and os.path.exists(SHIPPED)):
        return
    parts = _shipped('ghotel')
    out, st = RQ.plan_field('ghotel', parts, _art())
    assert out is not None and st['palettes'] == [8]
    assert st['cells'] == st['tiles'] == 348
    assert st['err_after'] <= 2.0
    a, b = parts[8], out[8]
    pa, s0, e0 = FN.parse_texture_block(a, 768)
    pb, s1, e1 = FN.parse_texture_block(b, 768)
    assert a[:s0] + a[e0:] == b[:s1] + b[e1:]          # records identical
    assert len(a) == len(b)
    # only palette 8's 255 non-key entries may change
    import ff7nx_marginblack as MB
    c0, h0, n0, k0 = MB.palette_colours(parts[3])
    c1, _h, _n, _k = MB.palette_colours(out[3])
    diff = {i for i in range(n0) if (c0[i] != c1[i]).any()}
    assert diff <= {8} and c0[8][0] == c1[8][0]


def test_lost_detail_restores_only_empty_texels():
    if not (_have() and os.path.exists(SHIPPED)):
        return
    parts = _shipped('wcrimb_2')
    provider = _art().provider
    c9 = LD._cosmos_chunk9(provider, 'wcrimb_2')
    assert c9
    plans, st = LD.plan_section9('wcrimb_2', parts[8], c9, provider)
    if not plans:                       # already repaired in this archive
        return
    new9, _n = LD.apply_plans(parts[8], plans)
    pl0, s0, e0 = FN.parse_texture_block(parts[8], 768)
    pl1, s1, e1 = FN.parse_texture_block(new9, 768)
    assert parts[8][:s0] + parts[8][e0:] == new9[:s1] + new9[e1:]
    for p0, p1 in zip(pl0, pl1):
        if p0 is None or p0.data == p1.data:
            continue
        a = np.frombuffer(p0.data, '<u2')
        b = np.frombuffer(p1.data, '<u2')
        changed = a != b
        assert (a[changed] == 0).all() and (b[changed] != 0).all()


def test_switches():
    os.environ[RQ.OFF_ENV] = '1'
    os.environ[LD.OFF_ENV] = '1'
    try:
        assert RQ.disabled() and LD.disabled()
    finally:
        del os.environ[RQ.OFF_ENV]
        del os.environ[LD.OFF_ENV]


if __name__ == '__main__':
    for k, v in sorted(globals().items()):
        if k.startswith('test_'):
            v()
            print('ok', k)
