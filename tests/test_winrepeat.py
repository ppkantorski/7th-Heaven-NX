"""BUILD 694: the window colour sliders step once per press, then repeat
(ff7nx_winrepeat). The stub and the shared cave run in unicorn; the real
0x6F5317 is replaced by a hook that answers from a scripted "held" state,
exactly as the recompiled one returns it (guest eax, its return slot
popped). The module is laid onto the dump when it is present.

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

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import ff7nx_winrepeat as W                                   # noqa: E402

unicorn = pytest.importorskip('unicorn')
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE   # noqa: E402
from unicorn.arm64_const import *                              # noqa: E402,F403

STATE = 0x4C2F8A0
CAVE = 0x998050
G = 0x2_0000_0000
CTX = 0x1_8000_0000
STACK = 0x1_9000_0000
ESP = 0x0FF00100
DUMP = os.path.join(HERE, 'dump', 'exefs', 'main')


class Rig:
    def __init__(self):
        uc = self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        uc.mem_map(0x990000, 0x10000)
        uc.mem_map(0xCA0000, 0x30000)                  # the sites, 0x6F5317
        uc.mem_map(0x10F0000, 0x10000)
        uc.mem_map(0x12CE000, 0x1000)
        uc.mem_map(STATE & ~0xFFF, 0x1000)
        uc.mem_map(G + 0x9A8000, 0x1000)
        uc.mem_map(G + 0x0FF00000, 0x10000)
        uc.mem_map(CTX, 0x1000)
        uc.mem_map(STACK - 0x10000, 0x11000)
        uc.mem_write(W.CTX_PTR, struct.pack('<Q', CTX))
        self.common = CAVE
        words = W.common_cave(CAVE, STATE)
        uc.mem_write(CAVE, b''.join(struct.pack('<I', x) for x in words))
        at = CAVE + 4 * len(words)
        self.sound = at
        sw = W.sound_cave(at, STATE)
        uc.mem_write(at, b''.join(struct.pack('<I', x) for x in sw))
        at += 4 * len(sw)
        self.stubs = {}
        for site, key in W.SITES:
            sw = W.stub(at, key, CAVE)
            uc.mem_write(at, b''.join(struct.pack('<I', x) for x in sw))
            self.stubs[site] = at
            at += 4 * len(sw)
        for site, _key in W.SITES:
            import a64 as A
            uc.mem_write(site, struct.pack('<I', A.bl(site, self.stubs[site])))
        self.held = 0
        self.calls = 0
        uc.hook_add(UC_HOOK_CODE, self._tr, begin=W.TRANSLATE, end=W.TRANSLATE)
        uc.hook_add(UC_HOOK_CODE, self._held, begin=W.HELD, end=W.HELD)

    def _tr(self, uc, addr, size, ud):
        uc.reg_write(UC_ARM64_REG_X0, G + (uc.reg_read(UC_ARM64_REG_X0) & 0xFFFFFFFF))
        for r in range(1, 19):
            uc.reg_write(UC_ARM64_REG_X0 + r, 0xDEAD0000 + r)
        uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_X30))

    def _held(self, uc, addr, size, ud):
        esp = struct.unpack('<I', uc.mem_read(CTX + 0x10, 4))[0]
        key = struct.unpack('<I', uc.mem_read(G + esp + 4, 4))[0]
        self.calls += 1
        uc.mem_write(CTX, struct.pack('<I', 1 if self.held & key else 0))
        uc.mem_write(CTX + 0x10, struct.pack('<I', esp + 4))      # pops its slot
        for r in range(0, 19):
            uc.reg_write(UC_ARM64_REG_X0 + r, 0xBEEF0000 + r)
        uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_X30))

    def frame(self, site, key, held, prev):
        """One menu frame asking `site`: True when the value moves."""
        self.held = held
        self.uc.mem_write(G + W.PRESSED, struct.pack('<I', held & ~prev))
        self.uc.mem_write(CTX + 0x10, struct.pack('<I', ESP - 4))  # ret slot pushed
        self.uc.mem_write(G + ESP, struct.pack('<II', key, 4))
        for k in range(19, 30):
            self.uc.reg_write(UC_ARM64_REG_X19 + k - 19, 0x5150 + k)
        sp = STACK - 0x200
        self.uc.reg_write(UC_ARM64_REG_SP, sp)
        self.uc.reg_write(UC_ARM64_REG_X30, 0)
        self.uc.emu_start(site, site + 4, count=10000)
        assert [self.uc.reg_read(UC_ARM64_REG_X19 + k) for k in range(11)] == \
            [0x5150 + k for k in range(19, 30)]
        assert self.uc.reg_read(UC_ARM64_REG_SP) == sp
        assert struct.unpack('<I', self.uc.mem_read(CTX + 0x10, 4))[0] == ESP
        return struct.unpack('<I', self.uc.mem_read(CTX, 4))[0] != 0

    def click(self, frame):
        """The sound check right after a "yes": does the cursor sound play
        with the guest frame word `frame`? (stock: frame & 4)"""
        self.uc.mem_write(G + ESP + 0x100, struct.pack('<H', frame & 0xFFFF))
        self.uc.reg_write(UC_ARM64_REG_X0, G + ESP + 0x100)
        self.uc.reg_write(UC_ARM64_REG_X9, 0x77)
        self.uc.reg_write(UC_ARM64_REG_X30, 0)
        self.uc.emu_start(self.sound, 0, count=100)
        w8 = self.uc.reg_read(UC_ARM64_REG_X8)
        assert w8 & ~4 == frame & 0xFFFF & ~4
        return bool(w8 & 4)


def run(r, site, key, pattern):
    """pattern: one bool per frame (held). The frames the value moved."""
    moved, prev = [], 0
    for f, h in enumerate(pattern):
        held = key if h else 0
        if r.frame(site, key, held, prev):
            moved.append(f)
        prev = held
    return moved


LEFT, RIGHT = W.SITES[0], W.SITES[1]


def test_a_tap_is_one_step():
    r = Rig()
    for n in (1, 2, 5, W.REPEAT_AFTER):
        assert run(r, *LEFT, [True] * n + [False] * 3) == [0], n


def test_a_hold_repeats_after_the_pause_every_frame():
    r = Rig()
    moved = run(r, *RIGHT, [True] * 40)
    assert moved == [0] + list(range(W.REPEAT_AFTER, 40))


def test_each_press_starts_again_and_the_sides_are_separate():
    r = Rig()
    pat = [True] * 30 + [False] + [True] * 3
    m = run(r, *LEFT, pat)
    assert m == [0] + list(range(W.REPEAT_AFTER, 30)) + [31]
    # right held long earlier does not make a later left tap repeat
    r = Rig()
    run(r, *RIGHT, [True] * 30)
    assert run(r, *LEFT, [True] * 3) == [0]


def test_every_row_uses_its_own_site():
    r = Rig()
    for site, key in W.SITES:
        assert run(r, site, key, [True] * 2 + [False]) == [0]
    assert r.calls == 3 * len(W.SITES)


def test_a_counter_left_over_from_another_screen_is_reset_by_the_press():
    r = Rig()
    r.uc.mem_write(STATE, struct.pack('<I', 500))       # stale: held elsewhere
    assert run(r, *LEFT, [True] * 3) == [0]


@pytest.mark.skipif(not os.path.exists(DUMP), reason='no dump')
def test_module_lays_out_on_the_dump(tmp_path):
    import nxmap
    assert W.check_sites(nxmap.Main(DUMP).img) == []
    rep = W.apply_to_nso(DUMP, str(tmp_path / 'main'), DUMP)
    img = nxmap.Main(str(tmp_path / 'main')).img
    import ff7nx_deadspace as DS
    lo, hi = DS.part(DUMP, 'winrep')
    assert rep['bytes'] <= hi - lo
    for site, _key in W.SITES:
        w = struct.unpack_from('<I', img, site)[0]
        assert site + 4 * (((w & 0x3FFFFFF) ^ 0x2000000) - 0x2000000) == rep['stubs'][site]
    for snd in W.SOUND:
        w = struct.unpack_from('<I', img, snd)[0]
        assert snd + 4 * (((w & 0x3FFFFFF) ^ 0x2000000) - 0x2000000) == rep['sound']


def test_every_press_clicks_and_the_repeat_keeps_the_stock_timing():
    r = Rig()
    for f0 in range(8):                       # whatever the frame counter
        prev = 0
        clicks = []
        for f in range(30):
            held = LEFT[1] if f < 26 else 0
            if r.frame(LEFT[0], LEFT[1], held, prev):
                clicks.append((f, r.click(f0 + f)))
            prev = held
        assert clicks[0] == (0, True), f0     # the tap always clicks
        for f, c in clicks[1:]:               # the repeat: 4 frames in 8
            assert c == bool((f0 + f) & 4), (f0, f)


def test_sound_checks_are_after_their_sites(tmp_path):
    assert len(W.SOUND) == len(W.SITES)
    if os.path.exists(DUMP):
        import nxmap
        img = nxmap.Main(DUMP).img
        assert W.check_sites(img) == []
        rep = W.apply_to_nso(DUMP, str(tmp_path / 'main'), DUMP)
        assert rep['bytes'] <= 256


def test_switch():
    assert W.enabled({}) and not W.enabled({W.ENV: '0'})
