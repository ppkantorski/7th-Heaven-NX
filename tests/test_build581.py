"""BUILD 581: palette-animated FX stays animated; sparse additive overlays
are not tiled vertically.

Runs on the real mod and the shipped archive when they are present.

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

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'tests'))
PROJ = os.path.expanduser('~/mnt/7th_heaven_nx')
if PROJ not in sys.path:
    sys.path.append(PROJ)

import lgp  # noqa: E402
import ff7nx_palanim as PA  # noqa: E402
import ff7nx_fxpages as XP  # noqa: E402
import ff7nx_parallaxfill as PF  # noqa: E402
from test_fxsplit import _art, _field, _have  # noqa: E402


def _rec(x, y, fx=1, blend=1):
    r = bytearray(52)
    struct.pack_into('<hh', r, 2, x, y)
    r[28] = fx
    r[30] = blend
    return bytes(r)


def test_sparse_additive_overlay_rule():
    # a foam band: 3 rows, none spans the width (x 0..96)
    band = [_rec(0, 0), _rec(96, 0), _rec(32, 32), _rec(64, 64)]
    s = b''.join(band)
    assert PF.sparse_additive_overlay(s, 0, len(band))
    # same shape but opaque: it is a backdrop, keep filling
    s = b''.join(_rec(x, y, 0, 0) for x, y in ((0, 0), (96, 0), (32, 32)))
    assert not PF.sparse_additive_overlay(s, 0, 3)
    # a full additive sheet (snow): every row spans the width
    s = b''.join(_rec(x, y) for y in (0, 32) for x in (0, 32, 64))
    assert not PF.sparse_additive_overlay(s, 0, 6)


def _layer(sec9, want):
    back, tex = sec9.find(b'BACK'), sec9.find(b'TEXTURE')
    for layer, _c, first, n in PF._layers(sec9, back, tex):
        if layer == want:
            return first, n
    raise AssertionError(want)


def test_real_fields_sparse_vs_backdrop():
    if not _have():
        return
    for name, layer, sparse in (('ujunon2', 4, True), ('las1_1', 4, True),
                                ('hyou1', 4, False), ('trnad_4', 3, False),
                                ('loslake1', 4, False), ('del3', 4, False)):
        s9 = lgp.split_sections(_field(name))[8]
        first, n = _layer(s9, layer)
        assert PF.sparse_additive_overlay(s9, first, n) is sparse, name


def test_script_animated_palettes():
    if not _have():
        return
    for name, want in (('ghotel', {8, 11}), ('sinbil_1', {6, 8}),
                       ('jet', {7, 8, 9, 12, 13})):
        got = PA.animated_palettes(lgp.split_sections(_field(name))[0])
        assert want <= set(got), (name, got)


def test_ghotel_animated_fog_is_not_made_static():
    if not _have():
        return
    parts = lgp.split_sections(_field('ghotel'))
    anim = PA.animated_palettes(parts[0])
    new9, st = XP.upgrade_section9('ghotel', parts[8], _art(), 768,
                                   animated=anim)
    assert st['anim_veto'] >= 1 and 'ghotel' in st['anim_names']
    os.environ[XP.KEEP_STATIC_ENV] = '1'
    try:
        _n, st2 = XP.upgrade_section9('ghotel', parts[8], _art(), 768,
                                      animated=anim)
    finally:
        del os.environ[XP.KEEP_STATIC_ENV]
    assert st2['anim_veto'] == 0


if __name__ == '__main__':
    for k, v in sorted(globals().items()):
        if k.startswith('test_'):
            v()
            print('ok', k)
