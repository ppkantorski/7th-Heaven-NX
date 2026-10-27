"""
ff7nx_titlemusic.py -- the title screen's music (BUILD 629).

WHAT FFNx DOES
==============
FFNx plays a track named `main_menu` from the external music folder while
the game is in MODE_MAIN_MENU (music.cpp, handle_mainmenu_playback) and
stops it when the mode changes. Echo-S ships `vgmstream/main_menu.ogg` (and
Nitpick's "Title Screen Music" another); on PC that is the title music.

THE SWITCH
==========
The port's ogg music is native: the game's play_midi (x86 0x742055) is a
host call into PLAY_NATIVE (0x10EC490), which resolves the track's name and
hands it to MusicPlayer::request(player, name, id, fade) (REQUEST, 0x2B40).
The player keeps one stream per id (128 slots) and only reads the name the
first time an id is played. So the title screen asks for its own id,
TRACK_ID = 127 -- no game track uses it (play ids stop at 98) -- with the
name "main_menu" directly: on the title screen's first frame a cave calls
REQUEST(player, "main_menu", 127, 0); when the title screen exits (the frame
[0xDD74E0] == -1 resets [0xDD76F8]) a second cave calls MusicPlayer::stop
(STOP, 0x2A80, as the native stop_midi does).

BUILD 629's first version called the game's own play_music (x86 0x7478BD)
instead. That is the legacy WAV player (`<dir>\\<name>.wav`): it found no
main_menu.wav and aborted on the title screen. Never route this through it.

Both hooks sit in the ARM body of x86 0x722393, the title screen's per-frame
function (New Game / Continue, and the load screen behind Continue -- the
music keeps playing there, as on PC). Both displaced instructions are
replayed; at both sites every caller-saved register is dead (the next
instructions reload what they use), and x19..x30 are saved by the cave.

The guest string lives at GUEST_NAME in ff7_en's .data, 16 zero bytes no
code or mod patch uses (checked against the patched file before writing).

ADDITIONAL CREDITS (BUILD 764c)
===============================
The title's third entry plays credits.mp4 (the port's 4:47 staff roll, with
its own music) in the port's OWN full-screen player: a scene method (vtable
0x126D3A0) at +0x6100 that opens the movie (0x1129F40), loops -- draw,
`finished?` (0x112A260 at +0x64CC), present, input -- until it ends or is
skipped, closes it and returns. Not fw_movie_* (BUILD 764/764b hooked those;
the hardware diagnostic showed the credits reach neither prepare nor update).
The music player runs on its own thread, so the title track played on.

Three hooks in that player, sharing guest bytes after the name
(GUEST_STATE: state, frames, saved volume), all gated on the player's
current id being TRACK_ID -- the ending movies and any other caller of the
player are left alone:

  ENTRY  +0x6100   save the music volume ([player+0x44c] x 127) and start
                   the player's own volume transition (0x2D50) to 1/127 over
                   FADE_OUT frames. NOT to 0: a transition that ends at 0
                   STOPS the current stream (MusicPlayer tick, +0x27E8).
  LOOP   +0x64CC   every movie frame: once the volume is under 1 % (or after
                   PAUSE_AFTER frames) the native pause_midi (0x10EC510).
  EXIT   +0x655C   (both the played and the could-not-open paths join here)
                   restart_midi (0x10EC540) if it was paused, then the
                   transition back to the saved volume over FADE_IN frames.

The 629 stop cave also puts the saved volume back at once if a state is
left, so New Game / Continue never inherit a faded volume.

SEVENTH_NX_TITLE_CREDITS_PAUSE=0 builds the 629 pair only.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os
import struct

import a64 as A
import ff7nx_cave
import ff7nx_audio_cave as AC
import nxmap

TRACK = 'main_menu.ogg'
TRACK_ID = 127                        # the player's last slot; game ids stop at 98
GUEST_NAME = 0x914B00                 # "main_menu\0" written into ff7_en
NAME = b'main_menu\x00'

PLAYER_PTR_PAGE = 0x12CE000
PLAYER_PTR_OFF = 0x238                # [[0x12CE238]] -> the MusicPlayer
REQUEST = 0x2B40                      # MusicPlayer::request(this, name, id, fade)
REQUEST_ORIG = 0xA9BD57F6             # stp x22, x21, [sp, #-0x30]!
STOP = 0x2A80                         # MusicPlayer::stop(this, 1)
STOP_CALLER = 0x10EC470               # the native stop_midi: ... mov w1, #1 ; b STOP

GUEST_STATE = GUEST_NAME + 0xA        # state, frames, saved volume, quiet (bytes)
ST_FADING, ST_PAUSED = 1, 2
FADE_OUT = 45                         # frames (x 16.67 ms in the player)
FADE_IN = 60
PAUSE_AFTER = 150                     # movie frames, if the volume never drops
VOL_TRANS = 0x2D50                    # MusicPlayer::set_volume_trans(this, v, t)
VOL_TRANS_ORIG = 0xF81D0FF5           # str x21, [sp, #-0x30]!
PAUSE_NATIVE = 0x10EC510              # pause_midi: current stream -> 0x37B0
PAUSE_NATIVE_ORIG = 0xA9BF7BFD
RESTART_NATIVE = 0x10EC540            # restart_midi: current stream -> 0x3750
RESTART_NATIVE_ORIG = 0xA9BF7BFD
CURRENT_OFF = 0x420                   # MusicPlayer: current id
VOLUME_OFF = 0x44C                    # MusicPlayer: music volume, 0..1
FADE_FLOOR = 1                        # transition target: 0 would stop the track
ENTRY_SITE = 0x6100                   # full-screen player: sub sp, sp, #0xa0
ENTRY_ORIG = 0xD10283FF
LOOP_SITE = 0x64CC                    # bl 0x112A260 (movie finished?), x0 = movie
LOOP_CALL = 0x112A260
EXIT_PLAYER_SITE = 0x655C             # ldp d9, d8, [sp, #0x30]
EXIT_PLAYER_ORIG = 0x6D4323E9
PAUSE_ENV = 'SEVENTH_NX_TITLE_CREDITS_PAUSE'
F127 = 0x42FE0000                     # 127.0f
F_SILENT = 0x3C23D70A                 # 0.01f


def credits_pause():
    return os.environ.get(PAUSE_ENV, '1').strip().lower() not in (
        '0', 'off', 'no', 'false')


def fcvtzs_w(rd, rn):
    return 0x1E380000 | (rn << 5) | rd


INIT_SITE = 0xE49180                  # ldr w8, [x23, #0x10] after 41B0D8(0xC8, 0x32)
INIT_ORIG = 0xB94012E8
EXIT_SITE = 0xE49274                  # mov w0, w19   ([DC12E4] = 0 on exit)
EXIT_ORIG = 0x2A1303E0


def _player(a):
    a.emit(A.adrp(0, a.pc(), PLAYER_PTR_PAGE))
    a.emit(A.ldr64(0, 0, PLAYER_PTR_OFF))
    a.emit(A.ldr64(0, 0, 0))


def build_play(resume):
    def build(cave, address):
        a = AC.Asm(cave, address)
        AC.save_host(a)
        AC.guest_ptr(a, GUEST_NAME)
        a.emit(A.mov_reg64(19, 0))                    # host "main_menu"
        _player(a)
        a.emit(A.mov_reg64(1, 19))
        a.emit(A.movz(2, TRACK_ID))
        a.emit(A.movz(3, 0))
        a.emit(A.bl(a.pc(), REQUEST))
        AC.restore_host(a)
        a.emit(INIT_ORIG)                             # the displaced ldr
        a.emit(A.b(a.pc(), resume))
        return a.resolve()
    return build


def _vol_trans(a, vol_reg, frames):
    """x0 = player; w1 = Wvol_reg; w2 = frames; bl MusicPlayer 0x2D50."""
    _player(a)
    a.emit(A.mov_reg(1, vol_reg))
    a.emit(A.movz(2, frames))
    a.emit(A.bl(a.pc(), VOL_TRANS))


def _save_args(a):
    """x0..x7 (a function entry's arguments) below the host frame."""
    a.emit(A.stp64_pre(0, 1, 31, -0x40))
    a.emit(A.stp64_off(2, 3, 31, 0x10))
    a.emit(A.stp64_off(4, 5, 31, 0x20))
    a.emit(A.stp64_off(6, 7, 31, 0x30))


def _restore_args(a):
    a.emit(A.ldp64_off(6, 7, 31, 0x30))
    a.emit(A.ldp64_off(4, 5, 31, 0x20))
    a.emit(A.ldp64_off(2, 3, 31, 0x10))
    a.emit(A.ldp64_post(0, 1, 31, 0x40))


def _ours(a, skip):
    """Branch to `skip` unless the player's current id is the title track."""
    _player(a)
    a.emit(A.ldr(8, 0, CURRENT_OFF))
    a.emit(A.cmp_imm(8, TRACK_ID))
    a.bcond(skip, 0x1)                                # b.ne


def build_entry(resume):
    """Full-screen player entry: save the volume, fade the title track."""
    def build(cave, address):
        a = AC.Asm(cave, address)
        _save_args(a)
        AC.save_host(a)
        _ours(a, 'done')
        AC.guest_ptr(a, GUEST_STATE)
        a.emit(A.mov_reg64(19, 0))
        a.emit(A.ldrb(8, 19, 0))
        a.cbnz(8, 'done')                             # already fading/paused
        _player(a)
        a.emit(A.ldr_s(0, 0, VOLUME_OFF))
        AC.mov32(a, 9, F127)
        a.emit(A.fmov_s_from_w(1, 9))
        a.emit(A.fmul_s(0, 0, 1))
        a.emit(A.fmov_s_imm(1, 0.5))
        a.emit(A.fadd_s(0, 0, 1))
        a.emit(fcvtzs_w(8, 0))
        a.emit(A.strb(8, 19, 2))
        a.emit(A.strb(31, 19, 1))
        a.emit(A.movz(8, ST_FADING))
        a.emit(A.strb(8, 19, 0))
        a.emit(A.movz(20, FADE_FLOOR))
        _vol_trans(a, 20, FADE_OUT)
        a.label('done')
        AC.restore_host(a)
        _restore_args(a)
        a.emit(ENTRY_ORIG)
        a.emit(A.b(a.pc(), resume))
        return a.resolve()
    return build


def build_loop(resume):
    """Every movie frame: pause the title track once it is silent."""
    def build(cave, address):
        a = AC.Asm(cave, address)
        _save_args(a)
        AC.save_host(a)
        _ours(a, 'done')
        AC.guest_ptr(a, GUEST_STATE)
        a.emit(A.mov_reg64(19, 0))
        a.emit(A.ldrb(8, 19, 0))
        a.emit(A.cmp_imm(8, ST_FADING))
        a.bcond('done', 0x1)                          # not fading
        a.emit(A.ldrb(9, 19, 1))
        a.emit(A.add_imm(9, 9, 1))
        a.emit(A.strb(9, 19, 1))
        a.emit(A.cmp_imm(9, PAUSE_AFTER))
        a.bcond('pause', 0xA)                         # b.ge
        _player(a)
        a.emit(A.ldr_s(0, 0, VOLUME_OFF))
        AC.mov32(a, 9, F_SILENT)
        a.emit(A.fmov_s_from_w(1, 9))
        a.emit(A.fcmp_s(0, 1))
        a.bcond('done', 0xA)                          # b.ge: still audible
        a.label('pause')
        a.emit(A.bl(a.pc(), PAUSE_NATIVE))
        a.emit(A.movz(8, ST_PAUSED))
        a.emit(A.strb(8, 19, 0))
        a.label('done')
        AC.restore_host(a)
        _restore_args(a)
        a.emit(A.bl(a.pc(), LOOP_CALL))               # the displaced call
        a.emit(A.b(a.pc(), resume))
        return a.resolve()
    return build


def build_exit_player(resume):
    """Full-screen player exit: resume and fade the title track back in."""
    def build(cave, address):
        a = AC.Asm(cave, address)
        _save_args(a)
        AC.save_host(a)
        AC.guest_ptr(a, GUEST_STATE)
        a.emit(A.mov_reg64(19, 0))
        a.emit(A.ldrb(20, 19, 0))
        a.cbz(20, 'done')
        a.emit(A.strb(31, 19, 0))
        a.emit(A.cmp_imm(20, ST_PAUSED))
        a.bcond('fade', 0x1)                          # b.ne: never paused
        a.emit(A.bl(a.pc(), RESTART_NATIVE))
        a.label('fade')
        a.emit(A.ldrb(20, 19, 2))
        _vol_trans(a, 20, FADE_IN)
        a.label('done')
        AC.restore_host(a)
        _restore_args(a)
        a.emit(EXIT_PLAYER_ORIG)
        a.emit(A.b(a.pc(), resume))
        return a.resolve()
    return build


def build_stop(resume, credits=False):
    def build(cave, address):
        a = AC.Asm(cave, address)
        AC.save_host(a)
        if credits:
            # BUILD 764: a faded-out title track gets its volume back at once
            AC.guest_ptr(a, GUEST_STATE)
            a.emit(A.mov_reg64(19, 0))
            a.emit(A.ldrb(8, 19, 0))
            a.cbz(8, 'clean')
            a.emit(A.strb(31, 19, 0))
            a.emit(A.ldrb(20, 19, 2))
            _vol_trans(a, 20, 0)                      # < 100 ms: immediate
            a.label('clean')
        _player(a)
        a.emit(A.movz(1, 1))
        a.emit(A.bl(a.pc(), STOP))
        AC.restore_host(a)
        a.emit(EXIT_ORIG)                             # the displaced mov
        a.emit(A.b(a.pc(), resume))
        return a.resolve()
    return build


def patch_exe(data, va_to_offset):
    """ff7_en with the guest string; refused if those bytes are in use."""
    off = va_to_offset(GUEST_NAME)
    if off is None:
        raise ValueError('%#x is not in ff7_en' % GUEST_NAME)
    cur = data[off:off + 16]
    if cur[:len(NAME)] == NAME:
        return data
    if cur != b'\x00' * 16:
        raise ValueError('ff7_en %#x is in use (%s)' % (GUEST_NAME, cur.hex()))
    out = bytearray(data)
    out[off:off + len(NAME)] = NAME
    return bytes(out)


def apply_to_nso(src, dest):
    with open(src, 'rb') as handle:
        blob = handle.read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    AC.expect_word(text, INIT_SITE, INIT_ORIG, 'title screen first frame')
    AC.expect_word(text, EXIT_SITE, EXIT_ORIG, 'title screen exit')
    AC.expect_word(text, REQUEST, REQUEST_ORIG, 'MusicPlayer::request')
    AC.expect_word(text, STOP_CALLER + 0xC, 0x320003E1, 'native stop_midi (orr w1, wzr, #1)')
    AC.expect_word(text, STOP_CALLER + 0x10, A.b(STOP_CALLER + 0x10, STOP), 'native stop_midi (b stop)')
    m = nxmap.Main(src)
    pool = ff7nx_cave.HolePool(text, starts=set(m.arm_starts))
    out = {}
    credits = credits_pause()
    sites = [(INIT_SITE, build_play(INIT_SITE + 4)),
             (EXIT_SITE, build_stop(EXIT_SITE + 4, credits))]
    if credits:
        AC.expect_word(text, ENTRY_SITE, ENTRY_ORIG, 'full-screen player entry')
        AC.expect_word(text, LOOP_SITE, A.bl(LOOP_SITE, LOOP_CALL), 'player loop: finished?')
        AC.expect_word(text, EXIT_PLAYER_SITE, EXIT_PLAYER_ORIG, 'full-screen player exit')
        AC.expect_word(text, VOL_TRANS, VOL_TRANS_ORIG, 'MusicPlayer volume trans')
        AC.expect_word(text, PAUSE_NATIVE, PAUSE_NATIVE_ORIG, 'pause_midi')
        AC.expect_word(text, RESTART_NATIVE, RESTART_NATIVE_ORIG, 'restart_midi')
        sites += [(ENTRY_SITE, build_entry(ENTRY_SITE + 4)),
                  (LOOP_SITE, build_loop(LOOP_SITE + 4)),
                  (EXIT_PLAYER_SITE, build_exit_player(EXIT_PLAYER_SITE + 4))]
    for site, builder in sites:
        entry, placed = ff7nx_cave.emit_laid_out(pool, builder)
        placed[site] = A.b(site, entry)
        out.update(placed)
    for where, word in out.items():
        struct.pack_into('<I', text, where, word)
    raw[0] = bytes(text)
    packed = AC.pack(blob, raw, 0)
    check_segs, check_raw = AC.segments(packed)
    assert check_segs[0][2] == segs[0][2]
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, 'wb') as handle:
        handle.write(packed)
    return {'main': dest, 'words': len(out)}
