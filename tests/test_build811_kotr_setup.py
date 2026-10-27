#!/usr/bin/env python3
"""BUILD 811: Knights of the Round's setup tick is drawn live.

While effect100 slot 99 runs run_kotr_camera_476AFB and the throttle's
cam_init (Cur + CUR_CAM_INIT) is 0 -- KOTR's first, initialising tick only --
paused draws are not interpolated. Every other state follows FFNx exactly,
as test_fxinterp checks. Runs the real fxinterp.bin in unicorn.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""
import os
import random
import struct
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
pytest.importorskip('unicorn')
import test_fxinterp as T                                      # noqa: E402
import ff7nx_fxinterp as FX                                    # noqa: E402

KOTR_CAM_FN = 0x476AFB
SLOT99 = 0xBF2858 + 4 * 99


def _frames(cam_fn, cam_init, draws=(('mat', T.RET0), ('mat', T.RET0 + 8),
                                     ('spt', T.RET0 + 16)), ticks=4):
    box = T.Box()
    ref = T.FFNx()
    box.g(SLOT99, struct.pack('<I', cam_fn))
    box.uc.mem_write(T.CUR + FX.CUR_CAM_INIT, bytes([cam_init]))
    rng = random.Random(1)
    live = interp = 0
    host_slot = T.CUR - 0xE0 + 2                   # KOTR's main loop, slot 2
    draws = list(draws)
    for f in range(ticks * T.FREQ):
        real = f % T.FREQ == 0
        k = f // T.FREQ + (0 if real else 1)
        exp = ref.call(draws, lambda i: T.state_values(rng, draws[i][0], k, i))
        box.cur(host_slot, (f % T.FREQ + 1) % T.FREQ)
        for i, (kind, ret) in enumerate(draws):
            now = T.state_values(rng, kind, k, i)
            got = box.draw(kind, ret, now)
            want = exp[i] if exp[i] is not None else now
            if got == now:
                live += not real
            if got == want:
                interp += 1
            yield f, i, kind, got, now, want
        box.clear()


def test_the_setup_tick_is_live():
    paused_moved = 0
    for f, i, kind, got, now, want in _frames(KOTR_CAM_FN, 0):
        assert got == now, 'frame %d draw %d (%s) was moved' % (f, i, kind)
        paused_moved += want != now
    assert paused_moved, 'FFNx would have moved these -- the test proves nothing'


@pytest.mark.parametrize('cam_init', [1, 2])
def test_kotr_after_its_setup_tick_follows_ffnx(cam_init):
    for f, i, kind, got, now, want in _frames(KOTR_CAM_FN, cam_init):
        assert got == want, 'frame %d draw %d (%s)' % (f, i, kind)


@pytest.mark.parametrize('fn', [0, 0x45CF2A, 0x476AFC])
def test_any_other_camera_follows_ffnx_even_at_cam_init_0(fn):
    for f, i, kind, got, now, want in _frames(fn, 0):
        assert got == want, 'frame %d draw %d (%s)' % (f, i, kind)


def test_the_offset_is_the_one_the_built_main_has():
    assert FX.CUR_CAM_INIT == 0x668
    assert FX.INTERP_CUR + FX.CUR_CAM_INIT == 0x3FECB10
