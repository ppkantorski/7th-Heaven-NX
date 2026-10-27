"""BUILD 629: the title screen's music (ff7nx_titlemusic).

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

HERE = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, HERE)

import ff7nx_titlemusic as T     # noqa: E402
import a64 as A                  # noqa: E402

DUMP_MAIN = os.path.join(HERE, 'dump', 'exefs', 'main')
DUMP_EXE = os.path.join(HERE, 'dump', 'romfs', 'ff7', 'resources', 'ff7_1.02', 'ff7_en')
MUSIC_IDX = os.path.join(HERE, 'dump', 'romfs', 'ff7', 'workingdir', 'data', 'music', 'music.idx')
need_dump = pytest.mark.skipif(not os.path.isfile(DUMP_MAIN), reason='needs the game dump')


@need_dump
def test_hooks_install(tmp_path):
    import nxmap
    import capstone
    out = tmp_path / 'main'
    r = T.apply_to_nso(DUMP_MAIN, str(out))
    assert r['words'] > 20
    m = nxmap.Main(str(out))
    cs = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    seen = {}
    sites = (T.INIT_SITE, T.EXIT_SITE, T.ENTRY_SITE, T.LOOP_SITE, T.EXIT_PLAYER_SITE)
    for site, resume in ((x, x + 4) for x in sites):
        a, calls = site, []
        for _ in range(200):
            i = next(cs.disasm(bytes(m.img[a:a + 4]), a))
            if i.mnemonic == 'bl':
                calls.append(int(i.op_str.strip('#'), 16))
            if i.mnemonic == 'b':
                a = int(i.op_str.strip('#'), 16)
                if a == resume:
                    break
            else:
                a += 4
        else:
            raise AssertionError('cave from %x never returns' % site)
        seen[site] = calls
    # the native music player, never the game's WAV player (x86 0x7478BD:
    # BUILD 629's first version aborted on the title screen through it)
    d = nxmap.Main(DUMP_MAIN)
    assert seen[T.INIT_SITE] == [0x10FC3A0, T.REQUEST]
    # BUILD 764: the exit puts a faded volume back before the stop
    assert seen[T.EXIT_SITE] == [0x10FC3A0, T.VOL_TRANS, T.STOP]
    # straight-line walks (BUILD 764c, the full-screen player)
    assert seen[T.ENTRY_SITE] == [0x10FC3A0, T.VOL_TRANS]
    assert seen[T.LOOP_SITE] == [0x10FC3A0, T.PAUSE_NATIVE, T.LOOP_CALL]
    assert seen[T.EXIT_PLAYER_SITE] == [0x10FC3A0, T.RESTART_NATIVE, T.VOL_TRANS]
    assert d.x86_to_arm[0x7478BD] not in sum(seen.values(), [])


@need_dump
def test_credits_pause_off_is_the_629_pair(tmp_path, monkeypatch):
    monkeypatch.setenv(T.PAUSE_ENV, '0')
    r = T.apply_to_nso(DUMP_MAIN, str(tmp_path / 'main'))
    import nxmap
    m = nxmap.Main(str(tmp_path / 'main'))
    d = nxmap.Main(DUMP_MAIN)
    for va in (T.ENTRY_SITE, T.LOOP_SITE, T.EXIT_PLAYER_SITE):
        assert m.img[va:va + 4] == d.img[va:va + 4]
    assert r['words'] > 20


def test_state_bytes_follow_the_name():
    assert T.GUEST_STATE >= T.GUEST_NAME + len(T.NAME)
    assert T.GUEST_STATE + 3 <= T.GUEST_NAME + 16


@need_dump
def test_own_slot_and_name():
    names = open(MUSIC_IDX, 'rb').read().decode().split('\r\n')
    assert len([n for n in names if n]) < T.TRACK_ID < 0x80    # past every game track, inside the player
    import exe_patch
    x = open(DUMP_EXE, 'rb').read()
    pe = exe_patch.parse_pe(x)
    y = T.patch_exe(x, lambda va: exe_patch.va_to_offset(pe, va))
    o = exe_patch.va_to_offset(pe, T.GUEST_NAME)
    assert y[o:o + len(T.NAME)] == T.NAME and T.patch_exe(y, lambda va: exe_patch.va_to_offset(pe, va)) == y


# ---------------------------------------------------------------- BUILD 764c
# The credits pause, executed: the real encoded caves on arm64emu, with the
# player and the three native calls modelled. The full-screen player's entry,
# its per-frame `finished?` call and its exit.
def _emu_world():
    import struct as _s
    import arm64emu as E
    mem = E.Mem()
    PLAYER = 0x5000000
    mem.setu(0x5200000, PLAYER, 8)
    mem.setu(T.PLAYER_PTR_PAGE + T.PLAYER_PTR_OFF, 0x5200000, 8)
    mem.setu(PLAYER + T.CURRENT_OFF, T.TRACK_ID, 4)
    mem.write(PLAYER + T.VOLUME_OFF, _s.pack('<f', 1.0))
    log = []

    def vol(cpu):
        v = cpu.x[1] & 0xFFFFFFFF
        log.append(('vol', v, cpu.x[2] & 0xFFFFFFFF))
        mem.write(PLAYER + T.VOLUME_OFF, _s.pack('<f', v / 127.0))

    def nat(name, ret=None):
        def f(cpu):
            log.append((name,) if ret is None else (name, cpu.x[0]))
            if ret is not None:
                cpu.x[0] = ret
        return f
    native = {T.VOL_TRANS: vol, T.PAUSE_NATIVE: nat('pause'),
              T.RESTART_NATIVE: nat('restart'), T.STOP: nat('stop'),
              T.LOOP_CALL: nat('finished?', 0)}
    return E, mem, PLAYER, log, native


def _place(builder):
    import ff7nx_cave
    import nxmap
    m = nxmap.Main(DUMP_MAIN)
    pool = ff7nx_cave.HolePool(bytearray(m.img), starts=set(m.arm_starts))
    return ff7nx_cave.emit_laid_out(pool, builder)


def _run(E, mem, native, entry, code, resume, x0=0x1111):
    cpu = E.Cpu(mem, native=native)
    cpu.sp = 0x6000000
    cpu.x[0], cpu.x[7] = x0, 0x7777
    if resume == T.EXIT_PLAYER_SITE + 4:
        # the replayed `ldp d9, d8` is outside the interpreter's subset: stop
        # on it (it is the cave's last word before the branch back)
        at = [va for va, w in code.items() if w == T.EXIT_PLAYER_ORIG]
        assert len(at) == 1
        assert cpu.run(entry, code, stop_at=at[0]) == at[0]
        nxt, pc = code.get(at[0] + 4), at[0] + 4         # then b ... b -> resume
        while nxt is not None:
            off = nxt & 0x3FFFFFF
            off = off - (1 << 26) if off & (1 << 25) else off
            assert nxt >> 26 == 0x5
            pc = pc + 4 * off
            nxt = code.get(pc)
        assert pc == resume
    else:
        assert cpu.run(entry, code) == resume
    # the entry's displaced word is `sub sp, sp, #0xa0`; the others keep sp
    want = 0x6000000 - (0xA0 if resume == T.ENTRY_SITE + 4 else 0)
    assert cpu.sp == want
    if resume != T.EXIT_SITE + 4:          # the 629 stop site has no live args
        assert cpu.x[7] == 0x7777
    return cpu


@need_dump
def test_credits_pause_executes():
    E, mem, PLAYER, log, native = _emu_world()
    en = _place(T.build_entry(T.ENTRY_SITE + 4))
    lp = _place(T.build_loop(T.LOOP_SITE + 4))
    ex = _place(T.build_exit_player(T.EXIT_PLAYER_SITE + 4))
    state = lambda: mem.read(E.Cpu(mem).guest_to_host(T.GUEST_STATE), 3)  # noqa: E731
    # entry over the title track: save 127, fade to 1/127 (0 would stop it)
    c = _run(E, mem, native, *en, T.ENTRY_SITE + 4)
    assert c.x[0] == 0x1111
    assert log == [('vol', T.FADE_FLOOR, T.FADE_OUT)] and state() == bytes([1, 0, 127])
    # every movie frame calls finished? with the movie in x0 (kept)
    c = _run(E, mem, native, *lp, T.LOOP_SITE + 4, x0=0xABCD)
    assert log[1:] == [('pause',), ('finished?', 0xABCD)] and state()[0] == 2
    assert c.x[0] == 0                                   # finished?'s result
    n = len(log)
    for _ in range(300):
        _run(E, mem, native, *lp, T.LOOP_SITE + 4, x0=0xABCD)
    assert all(e[0] == 'finished?' for e in log[n:])      # paused, nothing else
    # exit: restart and fade back to 127
    n = len(log)
    _run(E, mem, native, *ex, T.EXIT_PLAYER_SITE + 4)
    assert log[n:] == [('restart',), ('vol', 127, T.FADE_IN)] and state()[0] == 0
    n = len(log)
    _run(E, mem, native, *ex, T.EXIT_PLAYER_SITE + 4)
    assert len(log) == n                                  # once only


@need_dump
def test_other_tracks_are_left_alone():
    E, mem, PLAYER, log, native = _emu_world()
    en = _place(T.build_entry(T.ENTRY_SITE + 4))
    lp = _place(T.build_loop(T.LOOP_SITE + 4))
    mem.setu(PLAYER + T.CURRENT_OFF, 12, 4)               # an ending: game music
    _run(E, mem, native, *en, T.ENTRY_SITE + 4)
    _run(E, mem, native, *lp, T.LOOP_SITE + 4)
    assert log == [('finished?', 0x1111)]


@need_dump
def test_exit_restores_a_faded_title():
    E, mem, PLAYER, log, native = _emu_world()
    en = _place(T.build_entry(T.ENTRY_SITE + 4))
    _run(E, mem, native, *en, T.ENTRY_SITE + 4)
    st = _place(T.build_stop(T.EXIT_SITE + 4, True))
    _run(E, mem, native, *st, T.EXIT_SITE + 4)
    assert log[-2:] == [('vol', 127, 0), ('stop',)]
