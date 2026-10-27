#!/usr/bin/env python3
"""tests/test_camerahold.py -- the summon camera must not be dropped.

BUILD 379. `effect100-throttle` forces g_is_battle_paused on three frames in
four and every summon camera routine begins `if (g_is_battle_paused) return`,
so on those frames it aims nothing. The DEFAULT battle camera runs every
frame, so the viewer sees the two alternating at 15 Hz -- the operator's
"i see the summon then i see the regular field, back and forth rapidly".

FFNx's CameraInterpolationEffectDecorator writes g_battle_camera_position and
g_battle_camera_focal_point after EVERY call. This is that write, reduced to
a hold: capture on the real step, re-apply on the other three.

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
import sys

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import arm64emu as E                                           # noqa: E402
import ff7nx_dispatch as D                                     # noqa: E402

try:
    import ff7nx_dispatch_sites as S
except ImportError:                                            # pragma: no cover
    S = None

pytestmark = pytest.mark.skipif(S is None, reason='needs the sites file')

HOST = 0x40000000
PRE, POST = 0x900000, 0x901000
RAMUH = 0x00597206
CAM_BASE = 0x3FECAE8


def _thr():
    thr = dict(S.DISPATCH_SITES['effect100']['throttle'])
    thr['table'] = []
    # BUILD 384 renamed cam_save -> cam_next and added prev/init, and the
    # camera table gained a threshold class. This file keeps testing the HOLD
    # -- `cam_interp=False` -- because the hold is still a shipping mode
    # (SEVENTH_NX_FX_CAMERAHOLD=hold) and it is the one with hardware history.
    # Interpolation has its own file, tests/test_camerainterp.py.
    thr['camera'] = [(n, int(n.rsplit('_', 1)[-1], 16), 0)
                     for n in D.EFFECT100_CAMERA]
    thr.update(cam_pos_guest=0xBF2158, cam_foc_guest=0xBFB1A0,
               cam_pos_ptr=CAM_BASE, cam_foc_ptr=CAM_BASE + 8,
               cam_next=CAM_BASE + 16, cam_prev=CAM_BASE + 28,
               cam_init=CAM_BASE + 40, cam_interp=False,
               cam_slot_ptr=CAM_BASE + 48, cam_active=CAM_BASE + 56,
               cam_final=CAM_BASE + 58, cam_thr=CAM_BASE + 60)
    return thr


def _sites(thr):
    dh = S.DISPATCH_SITES['effect100']['disp_hook']
    pre = {'hook': thr['pre'], 'displaced': thr['pre_displaced'],
           'ctx_reg': thr['ctx_reg'], 'store_reg': thr['store_reg'],
           'idx_off': dh['idx_off']}
    return pre, dict(pre, hook=thr['post'], displaced=thr['post_displaced'])


def _cpu(mem, thr, pre_site, idx):
    cpu = E.Cpu(mem, host_base=HOST)
    cpu.sp = 0x800000000
    cpu.x[0] = RAMUH
    cpu.x[pre_site['ctx_reg']] = 0x30000000
    cpu.x[pre_site['store_reg']] = 0x1234
    mem.setu(0x30000000 + pre_site['idx_off'], idx, 4)
    return cpu


def _put(mem, host, v):
    for i, x in enumerate(v):
        mem.setu(host + 2 * i, x & 0xFFFF, 2)


def _get(mem, host):
    return tuple(mem.u(host + 2 * i, 2) for i in range(3))


def test_the_camera_is_held_across_the_three_paused_frames():
    """BUILD 380's property, still true in `=hold` mode."""
    thr = _thr()
    pre_site, post_site = _sites(thr)
    pre = D.build_throttle_pre_cave(PRE, pre_site, thr, S.PAUSED_GUEST,
                                    thr['mask_bits'])
    post = D.build_throttle_post_cave(POST, post_site, thr)
    mem = E.Mem()
    idx = 9
    mem.setu(thr['ctr'] + idx, D.CAM_MARK, 1)          # a registered camera
    probe = E.Cpu(mem, host_base=HOST)
    paused_h = probe.guest_to_host(S.PAUSED_GUEST)
    pos_h = probe.guest_to_host(thr['cam_pos_guest'])
    foc_h = probe.guest_to_host(thr['cam_foc_guest'])
    mem.setu(paused_h, 0, 1)

    REAL_POS, REAL_FOC = (100, 200, 300), (11, 22, 33)
    DEFAULT_POS, DEFAULT_FOC = (-1, -2, -3), (-4, -5, -6)

    seen = []
    for frame in range(8):
        cpu = _cpu(mem, thr, pre_site, idx)
        assert cpu.run(PRE, pre) == pre_site['hook'] + 4
        assert cpu.x[0] & 0xFFFFFFFF == RAMUH, frame     # the survivor check
        paused = mem.u(paused_h, 1)
        # Model the two writers: the summon routine only aims the camera when
        # it is not paused; the default battle camera aims it every frame.
        _put(mem, pos_h, DEFAULT_POS)
        _put(mem, foc_h, DEFAULT_FOC)
        if not paused:
            _put(mem, pos_h, REAL_POS)
            _put(mem, foc_h, REAL_FOC)
        cpu2 = E.Cpu(mem, host_base=HOST)
        cpu2.sp = 0x800000000
        assert cpu2.run(POST, post) == post_site['hook'] + 4
        seen.append((paused, _get(mem, pos_h), _get(mem, foc_h)))

    # Frame 0 is the real step (phase 0 -> 1). After it every frame must show
    # the real camera, never the default one.
    assert seen[0][0] == 0, 'the first frame should be the real step'
    for i, (paused, pos, foc) in enumerate(seen):
        assert pos == REAL_POS, ('frame %d shows %r, the default leaked'
                                 % (i, pos))
        assert foc == REAL_FOC, ('frame %d focal %r' % (i, foc))
    assert sum(1 for p, _, _ in seen if p) == 6, 'three paused frames in four'


def test_a_non_camera_slot_is_untouched():
    """The hold must not fire for the generic arm, or every effect in the
    game would have its camera pinned."""
    thr = _thr()
    pre_site, post_site = _sites(thr)
    pre = D.build_throttle_pre_cave(PRE, pre_site, thr, S.PAUSED_GUEST,
                                    thr['mask_bits'])
    post = D.build_throttle_post_cave(POST, post_site, thr)
    mem = E.Mem()
    idx = 3
    mem.setu(thr['ctr'] + idx, 0, 1)                   # generic slot
    probe = E.Cpu(mem, host_base=HOST)
    pos_h = probe.guest_to_host(thr['cam_pos_guest'])
    mem.setu(probe.guest_to_host(S.PAUSED_GUEST), 0, 1)
    for frame in range(4):
        cpu = _cpu(mem, thr, pre_site, idx)
        cpu.run(PRE, pre)
        _put(mem, pos_h, (7, 8, 9))
        cpu2 = E.Cpu(mem, host_base=HOST)
        cpu2.sp = 0x800000000
        cpu2.run(POST, post)
        assert _get(mem, pos_h) == (7, 8, 9), frame


@pytest.mark.parametrize('marker', [D.CAM_MARK, D.CAM_MARK + 1,
                                    D.CAM_MARK + 2, D.CAM_MARK + 3])
def test_the_function_pointer_survives_the_camera_arm(marker):
    thr = _thr()
    pre_site, _ = _sites(thr)
    pre = D.build_throttle_pre_cave(PRE, pre_site, thr, S.PAUSED_GUEST,
                                    thr['mask_bits'])
    mem = E.Mem()
    mem.setu(thr['ctr'] + 9, marker, 1)
    cpu = _cpu(mem, thr, pre_site, 9)
    assert cpu.run(PRE, pre) == pre_site['hook'] + 4
    assert cpu.x[0] & 0xFFFFFFFF == RAMUH
    assert cpu.x[pre_site['store_reg']] & 0xFFFFFFFF == 0x1234


def test_ramuh_is_in_the_table():
    assert any(va == RAMUH for _n, va, _c in _thr()['camera'])
    assert len(_thr()['camera']) == 25
