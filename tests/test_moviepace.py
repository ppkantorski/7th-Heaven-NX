"""BUILD 618z: ff7nx_moviepace -- background-movie fields at 60, the movie
at its own 30 fps (618z 2nd: consumed by the clock, not every field frame).

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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

_MAIN = os.path.join(os.path.dirname(__file__), '..', 'dump', 'exefs', 'main')
_BSS = 0x2400000
_MOVIE = 0x60000000


def _patched():
    import nxmap
    import ff7nx_moviepace as M
    if not os.path.exists(_MAIN):
        pytest.skip('no dump')
    m = nxmap.Main(_MAIN)
    assert M.check_anchors(m.text, log=lambda *_: None) == 0
    return M, M.build_patches(m.img, set(m.arm_starts), _BSS,
                              log=lambda *_: None)


def _btarget(word, va):
    imm = word & 0x3FFFFFF
    if imm & 0x2000000:
        imm -= 0x4000000
    return va + imm * 4


def _run(words, entry, w8, bg, limit=None, mode=1, now=0, due=0):
    import arm64emu
    import ff7nx_moviepace as M
    mem = arm64emu.Mem()
    cpu = arm64emu.Cpu(mem)
    mem.setu(cpu.guest_to_host(M.BGMOVIE_FLAG), bg, 1)
    mem.setu(cpu.guest_to_host(M.GAME_MODE), mode, 2)
    cpu.cntpct = now                           # mrs cntpct_el0
    mem.setu(_BSS, due, 8)
    mem.setu(_MOVIE + 8, w8, 1)                # the ready flag
    mem.setu(_MOVIE + 0x14, 7, 4)              # width, read before a redraw
    cpu.sp = 0x70000000
    cpu.set(8, w8)
    cpu.set(21, _MOVIE)
    cpu.set(23, _MOVIE + 8)
    if limit is not None:                      # the gate's x0: &field_limit_fps
        cpu.set(0, cpu.guest_to_host(0x9A0718))
        mem.setu(cpu.guest_to_host(0x9A0718), limit, 4)
    code = {va: w for va, w in words.items()
            if va not in (M.WAIT_HOOK, M.GATE_HOOK)}
    pc = cpu.run(entry, None, code=code, start_pc=entry, max_steps=200)
    return pc, cpu.x[8] & 0xFFFFFFFF, cpu.x[1] & 0xFFFFFFFF, mem.u(_BSS, 8)


def test_wait_cave_stock_for_other_movies():
    M, w = _patched()
    e = _btarget(w[M.WAIT_HOOK], M.WAIT_HOOK)
    assert _run(w, e, 1, 0)[0] == M.READY_PATH          # ready: as stock
    assert _run(w, e, 0, 0)[0] == M.WAIT_LOOP           # cutscene: waits
    assert _run(w, e, 0, 1, mode=3)[0] == M.WAIT_LOOP   # not the field
    assert _run(w, e, 1, 1, mode=3)[0] == M.READY_PATH


def test_wait_cave_background_movie_by_the_clock():
    M, w = _patched()
    e = _btarget(w[M.WAIT_HOOK], M.WAIT_HOOK)
    P = M.PERIOD
    # not ready: no wait, the stock path redraws
    assert _run(w, e, 0, 1, now=5 * P, due=4 * P)[0] == M.READY_PATH
    # first frame: consumed, due = now
    pc, _w8, _x1, due = _run(w, e, 1, 1, now=10 * P, due=0)
    assert (pc, due) == (M.READY_PATH, 10 * P)
    # one 60 Hz frame later: not due -> redraw the shown frame, due kept
    pc, _w8, x1, due = _run(w, e, 1, 1, now=10 * P + P // 2, due=10 * P)
    assert (pc, x1, due) == (M.NOT_READY_PATH, 7, 10 * P)
    # one period later (a little early is fine): consumed, due advances
    pc, _w8, _x1, due = _run(w, e, 1, 1, now=11 * P - 1000, due=10 * P)
    assert (pc, due) == (M.READY_PATH, 11 * P)
    # after a stall: resync to now
    pc, _w8, _x1, due = _run(w, e, 1, 1, now=20 * P, due=10 * P)
    assert (pc, due) == (M.READY_PATH, 20 * P)


def test_wait_cave_60hz_sequence_is_30fps():
    M, w = _patched()
    e = _btarget(w[M.WAIT_HOOK], M.WAIT_HOOK)
    due, taken = 0, 0
    frame = M.CPS // 60
    for i in range(1, 121):                    # two seconds of 60 Hz frames
        pc, _w8, _x1, due = _run(w, e, 1, 1, now=1000000 + i * frame, due=due)
        taken += pc == M.READY_PATH
    assert 59 <= taken <= 61


def test_gate_cave_limits_a_background_movie():
    M, w = _patched()
    e = _btarget(w[M.GATE_HOOK], M.GATE_HOOK)
    assert _run(w, e, 0, 0, limit=1)[:2] == (M.GATE_BACK, 1)
    assert _run(w, e, 0, 1, limit=0)[:2] == (M.GATE_BACK, 1)
    assert _run(w, e, 0, 0, limit=0)[:2] == (M.GATE_BACK, 0)


def test_loop_close_618z8_built_not_shipped(tmp_path):
    """last4_2 / last4_3: the engine restarts these BGMOVIE loops after
    decoded frame 38 (hardware 10-05). The re-timed loop is made at build
    time from the user's own Cosmos file with ffmpeg (no shipped video):
    39 frames at 30 fps, 10-bit, wrap step no larger than the loop's median
    step x 1.25; an unknown source is never replaced."""
    import subprocess
    import numpy as np
    import movies as M
    if not M.have_ffmpeg():
        pytest.skip('no ffmpeg')
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert not os.path.isdir(os.path.join(here, 'assets', 'movies'))
    for stem in M.LOOP_CLOSE_STEMS:
        src = os.path.join(here, 'cache', 'CosmosFMV30', 'Common', 'movies',
                           stem + '.avi')
        if not os.path.exists(src):
            continue
        a = M.loop_close(src, str(tmp_path), stem=stem)
        assert a and a.startswith(str(tmp_path)), a
        raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', a, '-f',
                              'rawvideo', '-pix_fmt', 'gray', '-'],
                             capture_output=True, check=True).stdout
        f = np.frombuffer(raw, np.uint8).reshape(-1, 448, 640).astype(float)
        assert len(f) == 39
        st = np.array([np.abs(f[(i + 1) % 39] - f[i]).mean()
                       for i in range(39)])
        assert st[-1] <= np.median(st) * 1.25, (stem, st[-1], np.median(st))
        assert M.probe(a)['pix_fmt'] == 'yuv420p10le'
        assert abs(M.probe(a)['fps'] - 30.0) < 0.01
    other = str(tmp_path / 'last4_2.mkv')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    'testsrc=size=64x48:rate=30', '-frames:v', '39',
                    '-pix_fmt', 'yuv420p', '-c:v', 'ffv1', other], check=True)
    assert M.loop_close(other, str(tmp_path), stem='last4_2') is None
    assert M.loop_close_wanted('last4_3') and not M.loop_close_wanted('opening')
