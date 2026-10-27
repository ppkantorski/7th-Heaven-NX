#!/usr/bin/env python3
"""
tests/test_moviecam_interp.py -- the 30 fps movie camera, interpolated.

BUILD 522. `_emplace_moviecam` used to write every 15 fps camera record twice
(r0 r0 r1 r1 ...), which fixed the budget/index/length but left the camera
on the 15 Hz beat while a true 30 fps movie moves every frame -- so the field
models composited over the movie stepped against it. Where the movie really
is 30 fps the in-between record is now blended.

What must hold, and is checked here:

  * nothing the game relies on moves: same length (so the same budget), every
    EVEN record byte-identical to the original, the same held tail;
  * cuts stay hard -- the real ones measured in the archive are held;
  * a blended record is a real camera halfway between its neighbours;
  * sentinels and gaps are never blended;
  * only tracks whose movie is a TRUE 30 fps source are interpolated;
  * the switch cannot invalidate a cached archive.

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
import types

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import ff7nx_moviecam as M                                     # noqa: E402

REC = M.REC


def rec(axes, pos, zoom=240, ramp=(0, 0), flag=0):
    return M.encode({'axes': [list(a) for a in axes], 'zdup': axes[2][2],
                     'pos': list(pos), 'ramp': list(ramp), 'zoom': zoom,
                     'flag': flag})


def rot_z(deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    r = lambda v: int(round(v * M.UNIT))
    return [(r(c), r(s), 0), (r(-s), r(c), 0), (0, 0, M.UNIT)]


def pan(n, deg_per_step=1.5, pos_step=40):
    return b''.join(rec(rot_z(i * deg_per_step), (i * pos_step, 0, 5000))
                    for i in range(n))


# ------------------------------------------------------------ the invariants

def test_length_even_records_and_tail_are_the_repeat_exactly():
    p = pan(20)
    rep = M.stretch_repeat(p, 2, 30)
    itp, blended, held = M.stretch_interpolated(p, 2, 30)
    assert len(itp) == len(rep)
    for k in range(20):
        assert itp[2 * k * REC:(2 * k + 1) * REC] == p[k * REC:(k + 1) * REC]
    assert itp[40 * REC:] == rep[40 * REC:]
    assert blended == 19            # every pair but the last is continuous


def test_the_budget_is_unchanged():
    """`size/4 - 2`, spent ten dwords a call (x86 0x40AFC9 / 0x40AD98)."""
    p = pan(50)
    assert len(M.stretch_interpolated(p, 2, 30)[0]) // 4 - 2 == \
        len(M.stretch_repeat(p, 2, 30)) // 4 - 2


def test_non_camera_entries_are_treated_exactly_as_before():
    """`list.txt` is not a whole number of records and nothing indexes it."""
    blob = b'list of movies\n' * 7
    assert M.stretch_interpolated(blob, 2, 30)[0] == \
        M.stretch_repeat(blob, 2, 30)


# ------------------------------------------------------------ the blend

def test_the_blend_is_halfway_in_angle_and_position():
    a, b = rec(rot_z(10), (100, 200, 5000)), rec(rot_z(12), (140, 260, 5100))
    m = M.decode(M.blend(a, b, 0.5))
    da, db = M._angle(M.decode(a), m), M._angle(m, M.decode(b))
    assert abs(da - 1.0) < 0.05 and abs(db - 1.0) < 0.05, (da, db)
    assert m['pos'] == [120, 230, 5050]


def test_the_blend_is_renormalised_not_a_shrunk_chord():
    """An unnormalised chord is a 0.5% zoom-in on every in-between frame."""
    m = M.decode(M.blend(rec(rot_z(0), (0, 0, 0)),
                         rec(rot_z(11), (0, 0, 0)), 0.5))
    for v in m['axes']:
        assert abs(math.sqrt(sum(c * c for c in v)) - M.UNIT) <= 2
    assert M.valid(M.encode(m))


def test_the_duplicated_z_component_is_kept_equal():
    """+0x12 equals axis Z.z on every one of the 12409 records measured."""
    m = M.decode(M.blend(rec(rot_z(0), (0, 0, 0)),
                         rec([(4096, 0, 0), (0, 4088, 256), (0, -256, 4088)],
                             (0, 0, 0)), 0.5))
    assert m['zdup'] == m['axes'][2][2]


# ------------------------------------------------------------ cuts stay hard

def test_a_rotation_cut_is_held():
    p = rec(rot_z(0), (0, 0, 0)) + rec(rot_z(40), (0, 0, 0))
    assert not M.blendable([p[:REC], p[REC:]], 0)


def test_a_position_spike_is_held_but_a_fast_steady_pan_is_not():
    steady = [rec(rot_z(0), (i * 900, 0, 0)) for i in range(6)]
    assert all(M.blendable(steady, k) for k in range(1, 4))
    spike = [rec(rot_z(0), (x, 0, 0)) for x in (0, 20, 40, 2000, 2020, 2040)]
    assert not M.blendable(spike, 2)


def test_two_cuts_back_to_back_are_both_held():
    """gold7 records 279 and 280. Against the LARGER neighbour each looks
    like 1x the other; against the smaller each is a spike."""
    xs = (0, 50, 100, 3000, 6400, 6450, 6500)
    r = [rec(rot_z(0), (x, 0, 0)) for x in xs]
    assert not M.blendable(r, 2) and not M.blendable(r, 3)


def test_zoom_ramp_and_flag_changes_are_held():
    base = rec(rot_z(0), (0, 0, 0))
    assert not M.blendable([base, rec(rot_z(0), (0, 0, 0), zoom=320)], 0)
    assert not M.blendable([base, rec(rot_z(0), (0, 0, 0), ramp=(0, 90))], 0)
    assert not M.blendable([base, rec(rot_z(0), (0, 0, 0), flag=1)], 0)
    assert M.blendable([base, rec(rot_z(0), (0, 0, 0), zoom=270)], 0)


def test_sentinels_and_gaps_are_never_blended():
    good = rec(rot_z(0), (0, 0, 0))
    sentinel = rec(rot_z(0), (0x7FFFFFFF, 0x7FFFFFFF, 0x7FFFFFFF), zoom=0xFFFF)
    gap = b'\x00' * (REC - 2) + b'\x01\x00'
    for other in (sentinel, gap):
        assert not M.blendable([good, other], 0)
        assert not M.blendable([other, good], 0)


# ------------------------------------------------------------ the real archive

MOVIECAM = None
for cand in (os.path.join(_HERE, 'moviecam.lgp'),
             os.path.join(_HERE, 'dump', 'romfs', 'ff7', 'workingdir', 'data',
                          'cd', 'moviecam.lgp'),
             os.path.join(os.path.dirname(os.path.dirname(_HERE)),
                          '7th_heaven_nx', 'dump', 'romfs', 'ff7', 'workingdir',
                          'data', 'cd', 'moviecam.lgp')):
    if os.path.exists(cand):
        MOVIECAM = cand
        break

needs_archive = pytest.mark.skipif(MOVIECAM is None,
                                   reason='needs the dumped moviecam.lgp')


@needs_archive
def test_every_track_keeps_its_shape_and_every_blend_is_a_camera():
    import lgp
    arc = lgp.Archive(MOVIECAM)
    total = 0
    for name, e in arc.index.items():
        p = e['payload']
        itp, blended, _ = M.stretch_interpolated(p, 2, 30)
        rep = M.stretch_repeat(p, 2, 30)
        assert len(itp) == len(rep), name
        if not p or len(p) % REC:
            assert itp == rep
            continue
        n = len(p) // REC
        for k in range(n):
            assert itp[2 * k * REC:(2 * k + 1) * REC] == \
                p[k * REC:(k + 1) * REC], (name, k)
            mid = itp[(2 * k + 1) * REC:(2 * k + 2) * REC]
            if mid != p[k * REC:(k + 1) * REC]:
                assert M.valid(mid), (name, k)
        total += blended
    assert total > 10000        # measured: 11368 of 12409 valid pairs


@needs_archive
@pytest.mark.parametrize('track,first_record_of_new_shot', [
    ('gold7.cam', 279), ('gold7.cam', 280), ('parashot.cam', 165),
    ('jairofly.cam', 495), ('last4_4.cam', 150), ('meteosky.cam', 199),
    ('biskdead.cam', 22), ('cscene2.cam', 30),
])
def test_the_measured_cuts_are_held(track, first_record_of_new_shot):
    import lgp
    p = lgp.Archive(MOVIECAM).index[track]['payload']
    recs = [p[i:i + REC] for i in range(0, len(p), REC)]
    assert not M.blendable(recs, first_record_of_new_shot - 1)


# ------------------------------------------------------------ which movies

@pytest.fixture(scope='module')
def b():
    import build
    return build


def test_only_a_true_30fps_source_is_interpolated(b, monkeypatch):
    rates = {'/m/junon.avi': 30.0, '/m/gold7.avi': 29.97,
             '/m/slow.avi': 15.0}

    def probe(path):
        if path == '/m/broken.avi':
            raise RuntimeError('ffprobe exploded')
        return {'fps': rates.get(path, 0.0)}
    monkeypatch.setattr(b.movie_convert, 'probe', probe)
    plan = types.SimpleNamespace(movies={
        'junon': ('/m/junon.avi', 'Cosmos'), 'gold7': ('/m/gold7.avi', 'C'),
        'slow': ('/m/slow.avi', 'x'), 'broken': ('/m/broken.avi', 'x')})
    got, _ = b._moviecam_true_rate_stems(
        plan, {'junon', 'gold7', 'slow', 'broken', 'fallpl'}, lambda s: None)
    # fallpl: no mod supplies it, so the build frame-doubles the game's own
    # 15 fps copy -- identical pictures in pairs, and the camera must match.
    assert got == {'junon', 'gold7'}


def test_the_switch_cannot_invalidate_a_cached_archive(b, tmp_path,
                                                      monkeypatch):
    van = tmp_path / 'van.lgp'
    van.write_bytes(b'v' * 8)
    env = b.MOVIECAM_INTERP_ENV
    assert env in b.ARCHIVE_NEUTRAL_ENV
    assert 'ff7nx_moviecam.py' in b.ARCHIVE_NEUTRAL_MODULES
    monkeypatch.setenv(env, '1')
    combined = b._archive_fingerprint('flevel.lgp', str(van), {}, 0)
    inputs = b._archive_inputs_fingerprint('flevel.lgp', str(van), {}, 0)
    monkeypatch.setenv(env, '0')
    assert b._archive_fingerprint('flevel.lgp', str(van), {}, 0) == combined
    assert b._archive_inputs_fingerprint('flevel.lgp', str(van), {}, 0) == \
        inputs


def test_it_is_on_by_default_and_off_says_off(b, monkeypatch):
    monkeypatch.delenv(b.MOVIECAM_INTERP_ENV, raising=False)
    assert b.moviecam_interp()
    monkeypatch.setenv(b.MOVIECAM_INTERP_ENV, '0')
    assert not b.moviecam_interp()
