"""BUILD 665: a frame with nothing drawn shows the last one (ff7nx_blankhold).

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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import arm64emu as E                                          # noqa: E402
import ff7nx_blankhold as B                                   # noqa: E402

STATE = 0x4C21B20
MODE = 0x6000000
AT = 0x99F700


class Rig:
    def __init__(self):
        self.mem = E.Mem()
        self.cpu = E.Cpu(self.mem)
        self.cpu.x[20] = MODE
        self.dw = B.draw_cave(AT, STATE)
        self.fw = B.flip_cave(AT + 0x40, STATE)

    def draw(self):
        self.cpu.sp = 0x7000000
        assert self.cpu.run(AT, self.dw) == B.DRAW + 4
        assert self.cpu.sp == 0x7000000 - 0x110

    def flip(self, mode):
        """-> the frame state the stock flip goes on to test"""
        self.mem.setu(MODE, mode, 4)
        assert self.cpu.run(AT + 0x40, self.fw) == B.FLIP_TEST + 4
        assert self.cpu.x[8] & 0xFFFFFFFF == self.mem.u(MODE, 4)
        assert self.cpu.x[20] == MODE
        return self.mem.u(MODE, 4)

    def frame(self, mode, draws):
        for _ in range(draws):
            self.draw()
        return self.flip(mode)


def test_drawn_frames_are_untouched():
    r = Rig()
    assert r.frame(3, 5) == 3
    assert r.frame(3, 1) == 3


def test_a_blank_frame_after_a_real_one_copies_the_kept_target():
    r = Rig()
    assert r.frame(2, 0) == 2              # nothing ever copied: stock
    assert r.frame(3, 10) == 3
    assert r.frame(2, 0) == 3              # held
    assert r.frame(1, 0) == 3              # held
    assert r.frame(2, 0) == 2              # a real black screen stays black
    assert r.frame(2, 0) == 2
    assert r.frame(3, 4) == 3
    assert r.frame(1, 0) == 3              # held again after a real frame
    assert r.mem.u(STATE + B.S_TOTAL, 4) == 3


@pytest.mark.parametrize('mode', [0, 4, 7])
def test_other_states_and_draws_without_a_clear_are_stock(mode):
    r = Rig()
    r.frame(3, 1)
    assert r.frame(mode, 0) == mode
    r.frame(3, 1)
    assert r.frame(2, 3) == 2              # drew, never cleared: stock


# --- BUILD 812: the world-map -> swirl handoff hold -------------------------

GAME = 0x6100000                      # the game-mode u32


class HandoffRig(Rig):
    def __init__(self):
        super().__init__()
        self.mem.setu(B.MODE_GOT, GAME, 8)
        self.pw = B.present_cave(AT + 0x200, STATE)
        self.now = 1000000

    def flip(self, mode):
        """-> w8, the state the stock flip tests (a held frame zeroes only
        w8: the stock skip path then stores 2 itself)"""
        self.mem.setu(MODE, mode, 4)
        assert self.cpu.run(AT + 0x40, self.fw) == B.FLIP_TEST + 4
        assert self.cpu.x[20] == MODE
        return self.cpu.x[8] & 0xFFFFFFFF

    def present_end(self):
        self.cpu.cntpct = self.now
        assert self.cpu.run(AT + 0x200, self.pw) == B.PRESENT_END + 4
        assert self.cpu.x[8] & 0xFFFFFFFF == 2

    def step(self, game_mode, ms=16.7, state=3, draws=100):
        """one frame: `ms` of game time since the last present ended ->
        the state the stock flip tests (0 = not presented)"""
        self.mem.setu(GAME, game_mode, 4)
        self.now += int(ms * 19200)
        self.cpu.cntpct = self.now
        st = self.frame(state, draws)
        self.present_end()
        return st


def test_a_steady_world_map_presents_every_frame():
    r = HandoffRig()
    assert [r.step(4) for _ in range(20)] == [3] * 20
    assert r.mem.u(STATE + B.S_HOLDS, 4) == 0


def test_the_frames_after_a_world_map_stall_are_not_presented():
    r = HandoffRig()
    for _ in range(5):
        r.step(4)
    assert r.step(4, ms=91) == 0
    assert r.step(4, ms=8.6) == 0
    assert r.step(4) == 3
    assert r.mem.u(STATE + B.S_HOLDS, 4) == 2


def test_a_short_hitch_is_presented():
    r = HandoffRig()
    r.step(4)
    assert r.step(4, ms=35) == 3


def test_the_first_two_swirl_frames_are_not_presented():
    r = HandoffRig()
    for _ in range(5):
        r.step(4)
    assert r.step(5, ms=87, draws=4) == 0
    assert r.step(5, ms=1, draws=4) == 0
    assert r.step(5, ms=16, draws=4) == 3
    assert r.step(5, ms=16, draws=4) == 3


def test_battle_field_and_menus_are_untouched_even_after_a_stall():
    r = HandoffRig()
    for mode in (1, 3, 0, 2):
        r.step(mode)
        assert r.step(mode, ms=200) == 3
    assert r.step(5) == 3                 # swirl not entered from the world map


def test_a_held_frame_does_not_count_as_blank_for_the_blank_hold():
    r = HandoffRig()
    r.step(4)
    assert r.step(4, ms=91) == 0
    assert r.mem.u(STATE + B.S_DRAWS, 4) == 0
    assert r.mem.u(STATE + B.S_TOTAL, 4) == 0


def test_no_present_yet_never_holds():
    r = HandoffRig()
    r.mem.setu(GAME, 4, 4)
    r.cpu.cntpct = 10 ** 9
    assert r.frame(3, 5) == 3
