"""BUILD 861b: ff7nx_floordepth MASK_FIELDS -- games: the cylinder face left
of the TV-station stairs hides the field shadows behind it.

Needs a games field: NX_GAMES_RAW (decompressed) or the dump's flevel.

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

import numpy as np
import pytest

import ff7nx_floordepth as F   # noqa: E402

FLEVEL = os.path.join(_nx_root, 'dump', 'romfs', 'ff7', 'workingdir', 'data',
                      'field', 'flevel.lgp')


@pytest.fixture(scope='module')
def games():
    import lgp
    p = os.environ.get('NX_GAMES_RAW')
    if p and os.path.exists(p):
        return list(lgp.split_sections(open(p, 'rb').read()))
    if not os.path.exists(FLEVEL):
        pytest.skip('needs NX_GAMES_RAW or the dump')
    a = lgp.Archive(FLEVEL)
    return list(lgp.split_sections(a.decompressed(a.index['games'])))


def _ids_at(s9, tiles):
    out = {}
    for layer, _c, first, n in F._rows(s9):
        for i in range(n):
            o = first + i * F.REC
            xy = struct.unpack_from('<hh', s9, o + 2)
            if xy in tiles and not (s9[o + 26] or s9[o + 28]):
                out.setdefault(xy, []).append(
                    (layer, struct.unpack_from('<h', s9, o + 24)[0]))
    return out


def test_mask_squares_take_the_cylinder_depth(games):
    cfg = F.MASK_FIELDS['games']
    new, n = F.plan_mask('games', games[8])
    assert n == 6
    got = _ids_at(new, set(cfg['tiles']))
    for xy in cfg['tiles']:
        far = [(l, i) for l, i in got[xy] if i in (4081, 4095)]
        assert not far, (xy, got[xy])
        assert (2, cfg['id']) in got[xy], (xy, got[xy])


def test_mask_draws_the_same_and_is_idempotent(games):
    import ff7nx_framesim as FS
    new, _n = F.plan_mask('games', games[8])
    again, n2 = F.plan_mask('games', new)
    assert n2 == 0 and again == new
    parts = list(games)
    parts[8] = new
    a, b = FS.Field(games), FS.Field(parts)
    for cam in ((-42, 89.5), (0, 0), (-120, 60)):
        assert np.array_equal(a.render(cam=cam, K=3, mark=False),
                              b.render(cam=cam, K=3, mark=False)), cam


def test_switch():
    os.environ[F.MASK_OFF_ENV] = '1'
    try:
        assert F.mask_disabled()
    finally:
        del os.environ[F.MASK_OFF_ENV]
    assert not F.mask_disabled()
