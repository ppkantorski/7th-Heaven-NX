#!/usr/bin/env python3
"""tests/test_satancam.py -- BUILD 745: Satan Slam's camera on the effect60
dispatcher, at the game's own rate and interpolated as FFNx does.

FFNx gives vincent_limit_satan_slam_camera_45CF2A a
CameraInterpolationEffectDecorator. This arms BUILD 384's camera decorator on
effect60's allow-list throttle for that one routine and holds the caves to:

  * the differential against FFNx's C++ (the transcription in
    test_camerainterp), frame by frame;
  * registration: 0x45CF2A gets CAM_MARK, the three BUILD 743 functions and
    the two aura functions stay throttled (0), anything else stays at full
    rate (0x80);
  * an allow-listed (marker 0) slot still takes the plain pause and gets its
    pause flag back; an unlisted (0x80) slot is never paused;
  * the effect100 caves are byte-identical to before (the one builder change
    is gated on a camera-only throttle).

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

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import arm64emu as E                                           # noqa: E402
import ff7nx_dispatch as D                                     # noqa: E402
import ff7nx_60fps as F                                        # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_camerainterp import FFNxCamera, _put, _get, FREQ     # noqa: E402

try:
    import ff7nx_dispatch_sites as S
except ImportError:                                            # pragma: no cover
    S = None

pytestmark = pytest.mark.skipif(S is None, reason='needs the sites file')

HOST = 0x40000000
PRE, POST = 0x900000, 0x901000
SATAN = 0x45CF2A
ALLOW = list(F.RECOMMENDED_THROTTLE_ONLY)


def _thr(interp=True):
    thr = dict(S.DISPATCH_SITES['effect60']['throttle'])
    thr['allow'] = True
    thr['table'] = [va for _n, va in thr.get('aura_allow', ())] + ALLOW
    old = os.environ.get(F.CAMERAHOLD_ENV)
    os.environ[F.CAMERAHOLD_ENV] = '1' if interp else 'hold'
    try:
        F._inject_effect60_camera(thr, log=lambda *_: None)
    finally:
        if old is None:
            del os.environ[F.CAMERAHOLD_ENV]
        else:
            os.environ[F.CAMERAHOLD_ENV] = old
    return thr


def _sites(thr):
    dh = S.DISPATCH_SITES['effect60']['disp_hook']
    pre = {'hook': thr['pre'], 'displaced': thr['pre_displaced'],
           'ctx_reg': thr['ctx_reg'], 'store_reg': thr['store_reg'],
           'idx_off': dh['idx_off']}
    return pre, dict(pre, hook=thr['post'], displaced=thr['post_displaced'])


class Rig:
    def __init__(self, interp=True, idx=7, marker=D.CAM_MARK):
        self.thr = _thr(interp)
        self.pre_site, self.post_site = _sites(self.thr)
        self.pre = D.build_throttle_pre_cave(
            PRE, self.pre_site, self.thr, S.PAUSED_GUEST,
            self.thr['mask_bits'])
        self.post = D.build_throttle_post_cave(POST, self.post_site, self.thr)
        self.mem = E.Mem()
        self.idx = idx
        self.mem.setu(self.thr['ctr'] + idx, marker, 1)
        self.mem.setu(self.thr['cam_thr'], D.CAM_THRESH_DEFAULT, 2)
        probe = E.Cpu(self.mem, host_base=HOST)
        self.paused_h = probe.guest_to_host(S.PAUSED_GUEST)
        self.pos_h = probe.guest_to_host(self.thr['cam_pos_guest'])
        self.foc_h = probe.guest_to_host(self.thr['cam_foc_guest'])
        self.slot_h = probe.guest_to_host(self.thr['data_base']
                                          + 0x20 * idx)
        self.mem.setu(self.paused_h, 0, 1)

    def frame(self, real_pos, real_foc, default=(-999, -888, -777),
              was_paused=0):
        self.mem.setu(self.paused_h, was_paused, 1)
        cpu = E.Cpu(self.mem, host_base=HOST)
        cpu.sp = 0x800000000
        cpu.x[0] = SATAN
        cpu.x[self.pre_site['ctx_reg']] = 0x30000000
        cpu.x[self.pre_site['store_reg']] = 0x1234
        self.mem.setu(0x30000000 + self.pre_site['idx_off'], self.idx, 4)
        assert cpu.run(PRE, self.pre) == self.pre_site['hook'] + 4
        assert cpu.x[0] & 0xFFFFFFFF == SATAN, 'fn pointer clobbered'
        assert cpu.x[self.pre_site['store_reg']] & 0xFFFFFFFF == 0x1234
        paused = self.mem.u(self.paused_h, 1)
        # Satan Slam's camera re-applies its OWN pose on a paused frame
        # (x86 0x45DB6B); the default camera then never shows. Model it as
        # writing the last real pose when paused.
        if paused:
            last = getattr(self, 'last', (default, default))
            _put(self.mem, self.pos_h, last[0])
            _put(self.mem, self.foc_h, last[1])
        else:
            _put(self.mem, self.pos_h, real_pos)
            _put(self.mem, self.foc_h, real_foc)
            self.last = (real_pos, real_foc)
        cpu2 = E.Cpu(self.mem, host_base=HOST)
        cpu2.sp = 0x800000000
        assert cpu2.run(POST, self.post) == self.post_site['hook'] + 4
        return ((_get(self.mem, self.pos_h), _get(self.mem, self.foc_h)),
                paused, self.mem.u(self.paused_h, 1))


def _differential(track, frames=24):
    rig = Rig()
    ref = FFNxCamera(D.CAM_THRESH_DEFAULT)
    adv = 0
    for f in range(frames):
        phase = ((f % FREQ) + 1) % FREQ
        if phase == 1:
            live = track[adv % len(track)]
            adv += 1
        got, paused, after = rig.frame(*live)
        assert (phase == 1) != bool(paused), (f, phase, paused)
        assert after == 0, 'frame %d: the pause flag was not restored' % f
        want = ref.frame(phase, *live)
        assert got == want, ('frame %d (phase %d): %r vs FFNx %r'
                             % (f, phase, got, want))


def test_the_camera_is_armed_only_for_satan_slam():
    thr = _thr()
    assert [va for _n, va, _t in thr['camera']] == [SATAN]
    assert SATAN not in thr['table']
    assert thr['data_base'] == 0xBFC3A0 and thr['stride'] == 0x20
    e100 = S.DISPATCH_SITES['effect100']['throttle']
    e100cam = (e100['model_next'] + 0x64 * 6 + 7) & ~7
    assert thr['cam_pos_ptr'] >= e100cam + 64, 'overlaps the summon camera'
    assert thr['cam_thr'] + 2 <= S.THROTTLE_BASE + S.BSS_GROW_THROTTLE


def test_a_rising_camera_matches_ffnx_every_frame():
    _differential([((100, -900 - 60 * i, 3000 - 40 * i),
                    (0, -400 + 25 * i, 0)) for i in range(8)])


def test_a_leftward_pan_matches_ffnx():
    _differential([((500 - 37 * i, -200 - 13 * i, 90 - 7 * i),
                    (-11 - 9 * i, -22 - 3 * i, -33 - i)) for i in range(6)])


def test_a_cut_snaps_like_ffnx():
    _differential([((0, 0, 0), (0, 0, 0)), ((30, 10, 5), (1, 2, 3)),
                   ((9000, -9000, 4000), (1, 2, 3)),
                   ((9010, -8990, 4005), (2, 3, 4))], frames=20)


def test_a_battle_that_was_already_paused_stays_paused():
    rig = Rig()
    for f in range(8):
        _got, _p, after = rig.frame((1, 2, 3), (4, 5, 6), was_paused=1)
        assert after == 1, 'frame %d unpaused a paused battle' % f


@pytest.mark.parametrize('marker,throttled', [(0, True), (0x80, False)])
def test_other_effect60_slots_keep_their_behaviour(marker, throttled):
    rig = Rig(idx=3, marker=marker)
    seen = []
    for _ in range(8):
        _got, paused, after = rig.frame((7, 8, 9), (1, 2, 3))
        seen.append(paused)
        assert after == 0
    assert any(seen) == throttled
    if throttled:
        assert seen.count(0) == 2, seen     # one real step in four


def _register(va, idx=11):
    CAVE = 0x902000
    thr = _thr()
    site = dict(S.DISPATCH_SITES['effect60']['add_hook'])
    words = D.build_addfn_cave(CAVE, site, None, thr['mask_bits'], thr)
    mem = E.Mem()
    mem.write(CAVE, struct.pack('<%dI' % len(words), *words))
    mem.setu(thr['cam_init'], 1, 1)
    mem.setu(thr['cam_thr'], 0xBEEF, 2)
    cpu = E.Cpu(mem, host_base=HOST)
    cpu.sp = 0x800000000
    for r in range(31):
        cpu.x[r] = E.GARBAGE
    cpu.x[site['ctx_reg']] = 0x30000000
    cpu.x[site['fn_val_reg']] = va
    cpu.x[0] = HOST + S.DISPATCH_SITES['effect60']['array_fn'] + 4 * idx \
        if 'array_fn' in S.DISPATCH_SITES['effect60'] else HOST
    mem.setu(0x30000000 + site['idx_off'], idx, 4)
    cpu.run(CAVE, words)
    return (mem.u(thr['ctr'] + idx, 1), mem.u(thr['cam_thr'], 2),
            mem.u(thr['cam_init'], 1))


def test_registration_marks_the_camera_and_keeps_the_allow_list():
    marker, thresh, init = _register(SATAN)
    assert marker == D.CAM_MARK, hex(marker)
    assert thresh == D.CAM_THRESH_DEFAULT and init == 0
    for va in ALLOW + [0x5C0AFF, 0x5C0300]:
        m, t, i = _register(va)
        assert m == 0, '0x%06X: 0x%02X' % (va, m)
        assert t == 0xBEEF and i == 1, 'a non-camera touched the camera BSS'
    for va in (0x45C263, 0x5BD96D, 0x425520):
        assert _register(va)[0] == 0x80


def test_effect100_caves_are_unchanged_by_the_builder_edit():
    import importlib.util
    path = os.path.join(_HERE, 'tests', 'fixtures', 'ff7nx_dispatch.py.pre745')
    if not os.path.exists(path):
        pytest.skip('no pre-745 builder to compare with')
    from importlib.machinery import SourceFileLoader
    spec = importlib.util.spec_from_file_location(
        'd_pre745', path, loader=SourceFileLoader('d_pre745', path))
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    import test_camerainterp as T
    thr = T._thr(True)
    thr['kotr'] = S.DISPATCH_SITES['effect100']['throttle'].get('kotr', ())
    pre, post = T._sites(thr)
    # BUILD 783 changed the camera arm on purpose (KOTR's first tick, 7 words
    # in the post cave). The pre-745 builder is compared with the pre-783
    # one -- still identical -- and the pre-783 one with today's: the pre
    # cave identical, the post cave exactly 7 words longer.
    p783 = os.path.join(_HERE, 'tests', 'fixtures', 'ff7nx_dispatch.py.pre783')
    spec = importlib.util.spec_from_file_location(
        'd_pre783', p783, loader=SourceFileLoader('d_pre783', p783))
    m783 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m783)
    for mod_a, mod_b in ((m783, old), (D, m783)):
        assert mod_a.build_throttle_pre_cave(PRE, pre, thr, S.PAUSED_GUEST,
                                             thr['mask_bits']) == \
            mod_b.build_throttle_pre_cave(PRE, pre, thr, S.PAUSED_GUEST,
                                          thr['mask_bits'])
    assert m783.build_throttle_post_cave(POST, post, thr) == \
        old.build_throttle_post_cave(POST, post, thr)
    assert len(D.build_throttle_post_cave(POST, post, thr)) == \
        len(m783.build_throttle_post_cave(POST, post, thr)) + 7
