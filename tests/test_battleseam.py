"""BUILD 730: battle background seams (sky prism overlap, art line).

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

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import ff7nx_battleseam as bs  # noqa: E402


def _prism(ys, vs, sides=8, r=30000.0):
    """An open prism wall, one ring per y, the way the stage skies are built:
    each face owns its own two columns of vertices (u 0..1 across a face)."""
    V, T, P = [], [], []
    for k in range(sides):
        a0, a1 = 2 * math.pi * k / sides, 2 * math.pi * (k + 1) / sides
        base = len(V)
        for y, v in zip(ys, vs):
            for a, u in ((a0, 0.0), (a1, 1.0)):
                V.append((round(r * math.cos(a)), y, round(r * math.sin(a))))
                T.append((u, v))
        for row in range(len(ys) - 1):
            i = base + 2 * row
            P.append((i, i + 1, i + 2))
            P.append((i + 1, i + 3, i + 2))
    nv, npo = len(V), len(P)
    h = [1, 1, 1, nv, 0, 0, nv, 0, 0, npo, 0, 0, 1, 1, 1, 0]
    d = bytearray(struct.pack('<16i', *h)).ljust(bs.HEADER, b'\0')
    d += b''.join(struct.pack('<3f', *v) for v in V)
    d += b''.join(struct.pack('<2f', *t) for t in T)
    d += b'\0' * (4 * npo)
    d += b''.join(struct.pack('<12H', 0, a, b, c, 0, 0, 0, a, b, c, 0, 0)
                  for a, b, c in P)
    d += b'\0' * 100
    d += struct.pack('<14i', 3, 0, npo, 0, nv, 0, 0, 0, 0, 0, 0, 0, 1, 1)
    return bytes(d)


UPPER = _prism([-14925, -9925, -4925, 75], [0.0, 0.2, 0.4, 0.6])
LOWER = _prism([-75, 4925, 9925, 14925], [0.0, 0.25, 0.5, 0.75])


def _ys(d):
    p = bs._parse(d)
    return sorted({v[1] for v in p['V']}), p


def test_overlap_band_is_closed_at_its_middle():
    fixed, done = bs.trim_overlaps({'qyao': UPPER, 'qyap': LOWER})
    assert done == [('qyao', 'qyap', 150.0)]
    ya, pa = _ys(fixed['qyao'])
    yb, pb = _ys(fixed['qyap'])
    assert ya[-1] == 0.0 and yb[0] == 0.0
    # the meeting rings are the same points: no gap, no shared band
    ka = sorted(tuple(v) for v in pa['V'] if v[1] == 0.0)
    kb = sorted(tuple(v) for v in pb['V'] if v[1] == 0.0)
    assert ka == kb
    # texture slides with the edge: 75 of 5000 units of the 0.2 v span
    for d, p, want in ((fixed['qyao'], pa, 0.6 - 0.2 * 75 / 5000),
                       (fixed['qyap'], pb, 0.25 * 75 / 5000)):
        for i, v in enumerate(p['V']):
            if v[1] == 0.0:
                vv = struct.unpack_from('<f', d, p['to'] + 8 * p['tex'][i] + 4)[0]
                assert abs(vv - want) < 1e-6
    # nothing but the ring moved
    assert len(fixed['qyao']) == len(UPPER)
    diff = [i for i in range(len(UPPER)) if fixed['qyao'][i] != UPPER[i]]
    assert diff and min(diff) >= bs.HEADER


def test_touching_floor_and_other_stages_are_left_alone():
    floor = _prism([75, 75], [0, 0])          # flat ring, no wall partners
    other = _prism([-75, 4925], [0, 1])       # same shape, other stage
    fixed, done = bs.trim_overlaps({'qyao': UPPER, 'qyaq': floor,
                                    'rzap': other, 'qyaa': b'not a p'})
    assert fixed == {} and done == []
    # already meeting edge to edge: nothing to do
    flush = _prism([75, 4925, 9925], [0, 0.5, 1])
    assert bs.trim_overlaps({'qyao': UPPER, 'qyap': flush}) == ({}, [])


def test_fix_is_idempotent():
    fixed, _ = bs.trim_overlaps({'qyao': UPPER, 'qyap': LOWER})
    again, done = bs.trim_overlaps(fixed)
    assert again == {} and done == []


def _tile(line):
    w, h = 1024, 64
    b = bytearray()
    for y in range(h):
        for x in range(w):
            g = 60 + (x * 3 + y) % 40
            if x == bs.LINE_COLUMN:
                g += line
            b += bytes((g, g, g, 255))
    return bytes(b), w, h


def test_art_line_is_painted_out():
    rgba, w, h = _tile(80)
    out, n = bs.fix_column_line(rgba, w, h)
    assert n == h
    c = bs.LINE_COLUMN
    for y in range(h):
        r = (y * w + c) * 4
        assert out[r] == (out[r - 4] + out[r + 4] + 1) // 2
        assert out[r + 3] == 255
    # every other pixel untouched, and a second pass is a no-op
    assert sum(a != b for a, b in zip(rgba, out)) <= 3 * h
    assert bs.fix_column_line(out, w, h) == (out, 0)


def test_art_without_the_line_is_untouched():
    rgba, w, h = _tile(0)
    assert bs.fix_column_line(rgba, w, h) == (rgba, 0)
    assert bs.fix_column_line(b'\0' * 512 * 4 * 4, 512, 4)[1] == 0


BATTLE = os.path.join(os.path.dirname(HERE), 'game_data_files', 'battle',
                      'battle', 'battle.lgp')


@pytest.mark.skipif(not os.path.isfile(BATTLE), reason='no battle.lgp')
def test_vanilla_battle_lgp_finds_exactly_the_four_skies():
    import lgp
    a = lgp.Archive(BATTLE)
    files = {e['name'].lower(): e['payload'] for e in a.entries}
    fixed, done = bs.trim_overlaps(files)
    assert done == [('puao', 'puap', 150.0), ('qyao', 'qyap', 150.0),
                    ('qzao', 'qzap', 62.0), ('riao', 'riap', 75.0)]
    assert sorted(fixed) == ['puao', 'puap', 'qyao', 'qyap',
                             'qzao', 'qzap', 'riao', 'riap']


def _disc(us, r=30062.0, y=14875.0, sides=8):
    """A flat floor disc whose vertices all sample (nearly) one texel."""
    V = [(0.0, y, 0.0)] + [(r * math.cos(2 * math.pi * k / sides), y,
                            r * math.sin(2 * math.pi * k / sides))
                           for k in range(sides)]
    T = [(us[k % len(us)], 0.797) for k in range(len(V))]
    P = [(0, 1 + k, 1 + (k + 1) % sides) for k in range(sides)]
    nv, npo = len(V), len(P)
    h = [1, 1, 1, nv, 0, 0, nv, 0, 0, npo, 0, 0, 1, 1, 1, 0]
    d = bytearray(struct.pack('<16i', *h)).ljust(bs.HEADER, b'\0')
    d += b''.join(struct.pack('<3f', *v) for v in V)
    d += b''.join(struct.pack('<2f', *t) for t in T)
    d += b'\0' * (4 * npo)
    d += b''.join(struct.pack('<12H', 0, a, b, c, 0, 0, 0, a, b, c, 0, 0)
                  for a, b, c in P)
    d += b'\0' * 100
    d += struct.pack('<14i', 3, 0, npo, 0, nv, 0, 0, 0, 0, 0, 0, 0, 1, 2)
    return bytes(d)


def _uvs(d):
    p = bs._parse(d)
    return {struct.unpack_from('<2f', d, p['to'] + 8 * t)
            for t in p['tex'].values()}


def test_floor_disc_samples_one_spot():
    disc = _disc([0.988, 0.992])
    fixed, names = bs.flatten_floors({'qyba': disc})
    assert names == ['qyba']
    (u, v), = _uvs(fixed['qyba'])
    assert abs(u - 0.990) < 1e-6 and abs(v - 0.797) < 1e-6
    assert len(fixed['qyba']) == len(disc)
    # idempotent
    assert bs.flatten_floors(fixed) == ({}, [])


def test_painted_and_small_surfaces_keep_their_uvs():
    wide = _disc([0.0, 0.5])                 # really textured
    small = _disc([0.988, 0.992], r=3000.0)  # a prop, not a floor
    assert bs.flatten_floors({'qyba': wide, 'qybb': small}) == ({}, [])
    # the sky walls are textured: left alone
    assert bs.flatten_floors({'qyao': UPPER, 'qyap': LOWER}) == ({}, [])


@pytest.mark.skipif(not os.path.isfile(BATTLE), reason='no battle.lgp')
def test_vanilla_floor_discs():
    import lgp
    a = lgp.Archive(BATTLE)
    files = {e['name'].lower(): e['payload'] for e in a.entries}
    fixed, done, floors, grounds = bs.fix_stages(files)
    assert 'qyba' in floors and 'puar' in floors and len(floors) == 37
    assert len(_uvs(fixed['qyba'])) == 1
    files.update(fixed)
    assert bs.fix_stages(files) == ({}, [], [], {})


def _quad(x0, x1, z0=-5000.0, z1=5000.0, y=0.0):
    V = [(x0, y, z0), (x1, y, z0), (x1, y, z1), (x0, y, z1)]
    P = [(0, 1, 2), (0, 2, 3)]
    h = [1, 1, 1, 4, 0, 0, 4, 0, 0, 2, 0, 0, 1, 1, 1, 0]
    d = bytearray(struct.pack('<16i', *h)).ljust(bs.HEADER, b'\0')
    d += b''.join(struct.pack('<3f', *v) for v in V)
    d += b''.join(struct.pack('<2f', v[0] / 1e4, v[2] / 1e4) for v in V)
    d += b'\0' * 8
    d += b''.join(struct.pack('<12H', 0, a, b, c, 0, 0, 0, a, b, c, 0, 0)
                  for a, b, c in P)
    d += b'\0' * 100
    d += struct.pack('<14i', 3, 0, 2, 0, 4, 0, 0, 0, 0, 0, 0, 0, 1, 0)
    return bytes(d)


def test_ground_joins_get_layers_and_the_battle_floor_stays_put():
    files = {'olam': _quad(-6000, 6000),      # under the origin
             'olaq': _quad(5800, 20000),      # 200-wide overlap strip
             'olar': _quad(19800, 30000),     # overlaps olaq only
             'olaz': _quad(-6000, 6000, y=-900)}  # another height: ignored
    out, layers = bs.layer_grounds(files)
    assert layers == {'olaq': 1}
    p = bs._parse(out['olaq'])
    assert {v[1] for v in p['V']} == {-bs.GROUND_LIFT}
    assert bs.layer_grounds({**files, **out}) == ({}, {})


def test_stacked_ground_layers_are_not_touched():
    files = {'lqap': _quad(-6000, 6000), 'lqaq': _quad(-6000, 6000)}
    assert bs.layer_grounds(files) == ({}, {})


@pytest.mark.skipif(not os.path.isfile(BATTLE), reason='no battle.lgp')
def test_vanilla_beach_layers():
    import lgp
    a = lgp.Archive(BATTLE)
    files = {e['name'].lower(): e['payload'] for e in a.entries}
    _f, _d, _fl, grounds = bs.fix_stages(files)
    assert grounds['olaq'] == 1 and grounds['olas'] == 1
    assert 'olam' not in grounds and 'olar' not in grounds
