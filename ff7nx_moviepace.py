#!/usr/bin/env python3
r"""
ff7nx_moviepace.py -- a background-movie field runs at 60, the movie at its
own rate. BUILD 618z.

THE REPORT (las4_2 / las4_3, hardware 10-05)
============================================
"i know we limit the fps during fmvs to 30 ... is it possible to keep
rendering at 60fps for all field models and the field while rendering fmvs
at their designed refresh rate?" las4_2 and las4_3 are fields you walk
around in over a looping background movie (BGMOVIE 1), and they run at 30.

WHY THEY RUN AT 30
==================
Two things, both measured in this module (ff7nx_dispatch documents the
first in full):

1. `update_movie_sample` is the port's native `fw_movie_update` (+0x10F1590).
   It BLOCKS until the decoder thread has the next frame ready:

     +0x10F1610  x23 = frame + 8           ; the ready flag
     +0x10F1614  ldarb w8, [x23]
     +0x10F1618  tbnz  w8, #0, +0x10F1650  ; ready -> upload, draw, consume
     +0x10F161C  wait: movie_is_finished? / pump / present / until ready

   The field calls it once a frame, so the field ticks once per movie frame:
   30 times a second with the 30 fps movie set.

2. The field limiter (x86 0x6384E6) skips itself while a movie plays unless
   `field_limit_fps` (guest 0x9A0718) is set:

     +0x9E5C08  w0 = 0x9A0718 ; bl translate
     +0x9E5C14  ldr  w8, [x0]
     +0x9E5C24  cbz  w8, +0x9E601C          ; 0 -> no limiting at all

   The movie opener (x86 0x40AFE5) sets it only for five movie numbers --
   on disc 2, 0x14/0x15 among them, which are exactly las4_2's and las4_3's
   PMVIE numbers; on disc 1 only 0x2A. So on disc 1 these two fields fall
   back to the movie's pace.

WHAT THIS DOES (only while BGMOVIE_flag, guest 0xCC0DC2, is set)
================================================================
* The limiter treats a background movie as frame-limited: `field_limit_fps
  || BGMOVIE_flag`. The field is then paced by the ordinary field limiter
  (with ff7nx_fieldpace on top), exactly like any field.
* `fw_movie_update` does not wait for a frame that is not ready yet. Its own
  not-ready path then runs: it redraws the frame it already has
  (+0x5C90 with w3 = 1, the same call it makes when a movie ends) and
  returns. When the decoder's next frame IS ready it is consumed as before.
  The decoder is wall-clock paced, so the picture still changes 30 times a
  second, now shown across two 60 Hz field frames.

Every other movie -- the cutscenes with models composited over them, whose
scripts are timed by field ticks during the movie -- is untouched and keeps
its pacing exactly (BGMOVIE_flag is 0 for them). Making those render at 60
needs the field's logic and drawing separated (logic at the movie's pace,
drawing interpolated at 60), which is a separate piece of work.

The pace of a background-movie field becomes the normal field pace: Cloud
walks at the speed he walks everywhere else (at 30 ticks a second he walked
at half the 60 FPS mod's speed, the way the 15 fps movie made PC FF7 walk
at half speed in these fields on disc 1).

BUILD 618z (2nd): THE MOVIE PLAYED AT DOUBLE SPEED (hardware 10-05)
=================================================================
The decoder is NOT wall-clock paced: it decodes the next frame as soon as
the last one was consumed, so with the field at 60 the frame was ready on
almost every field frame and the movie ran at 60 frames a second, twice its
rate. The wait cave now consumes by the clock: a ready frame is taken only
when at least one movie period (1/30 s, less 4 ms of tolerance) has passed
since the last one was due, and the due time advances by exactly one period
(it resyncs after a stall of two periods). Otherwise the stock not-ready
path redraws the frame already shown.

618z (3rd): the first version read the field frame's start time (guest
0xCFF8D8) as its clock; on hardware the movie still ran at 2x, so that word
is not the per-frame clock in this mode (nothing in the module names it
directly -- the fieldpace doc's address was an inference). The clock is now
the ARM generic timer itself (`mrs cntpct_el0`, 19.2 MHz on Switch, the
counter ff7nx_frameprobe reads on hardware): real time, independent of the
field loop and of how often the movie update is called per frame. 8 bytes of module BSS hold
the due time. The movie therefore plays at its own 30 fps while the field
draws at 60 -- no re-encode needed.

SEVENTH_NX_BGMOVIE_60=0 disables.
"""
from __future__ import annotations

import os
import shutil
import struct
import tempfile
from pathlib import Path

import a64 as A

ENV = 'SEVENTH_NX_BGMOVIE_60'
TRANSLATE = 0x10FC3A0
BGMOVIE_FLAG = 0xCC0DC2
GAME_MODE = 0xCBF9DC          # FF7 _mode (u16)
MODE_FIELD = 1

# hook 1: fw_movie_update's wait
WAIT_HOOK = 0x10F1618          # tbnz w8, #0, +0x10F1650
WAIT_LOOP = 0x10F161C          # the stock wait loop
READY_PATH = 0x10F1650         # upload/draw/consume, or redraw if not ready
NOT_READY_PATH = 0x10F1660     # the stock redraw of the shown frame (w1 =
                               # [x21, #0x14] must be loaded, as at 0x10F1658)
MRS_CNTPCT = 0xD53BE020        # mrs Xt, cntpct_el0 (19.2 MHz on Switch; the
                               # counter ff7nx_frameprobe reads on hardware)
CPS = 19200000
FPS_ENV = 'SEVENTH_NX_BGMOVIE_FPS'   # the background movie's own rate
MOVIE_FPS = 30                 # Cosmos FMV30's last4_2/3: 39 frames, 1.3 s
PERIOD = CPS // MOVIE_FPS      # 640000


def movie_fps(env=None):
    v = (env if env is not None else os.environ).get(FPS_ENV, '').strip()
    try:
        f = int(v) if v else MOVIE_FPS
    except ValueError:
        f = MOVIE_FPS
    return min(max(f, 5), 60)
TOLERANCE = CPS * 4 // 1000    # 4 ms
BSS_BYTES = 8                  # u64: when the next movie frame is due
# hook 2: the field limiter's movie gate
GATE_HOOK = 0x9E5C14           # ldr w8, [x0]   (field_limit_fps)
GATE_BACK = 0x9E5C18

ANCHORS = [
    (0x10F1610, 0x910022B7, 'add x23, x21, #8      the ready flag'),
    (0x10F1614, 0x08DFFEE8, 'ldarb w8, [x23]'),
    (0x10F1618, 0x370001C8, 'tbnz w8, #0, +0x10F1650'),
    (0x10F161C, 0xAA1303E0, 'mov x0, x19           wait loop'),
    (0x10F1650, 0x910022A8, 'add x8, x21, #8       ready path re-reads'),
    (0x10F1660, 0x7100043F, 'cmp w1, #1            not ready: width ok?'),
    (0x10F167C, 0x320003E3, 'mov w3, #1            redraw last frame'),
    (0x10F1684, 0x97BC5183, 'bl +0x5C90            draw'),
    (0x9E5C08, 0x5280E300, 'mov w0, #0x718'),
    (0x9E5C0C, 0x72A01340, 'movk w0, #0x9a, lsl #16   0x9A0718'),
    (0x9E5C10, 0x941C59E4, 'bl translate'),
    (0x9E5C14, 0xB9400008, 'ldr w8, [x0]          field_limit_fps'),
    (0x9E5C18, 0x7100011F, 'cmp w8, #0'),
    (0x9E5C24, 0x34001FC8, 'cbz w8, +0x9E601C     no limiter'),
]


def enabled(env=None):
    v = (env if env is not None else os.environ).get(ENV, '1')
    return str(v).strip().lower() not in ('0', 'off', 'false', 'no')


def w32(t, va):
    return struct.unpack_from('<I', t, va)[0]


def _fmt(word):
    return ' '.join('%02X' % b for b in struct.pack('<I', word))


def check_anchors(t, log=print):
    bad = 0
    for va, want, what in ANCHORS:
        got = w32(t, va)
        if got != want:
            log('  ! +%#09x is %08X, expected %08X (%s)' % (va, got, want,
                                                          what))
            bad += 1
    return bad


def _ubfx0(rd, rn):
    return 0x53000000 | (rn << 5) | rd          # ubfx wd, wn, #0, #1


def wait_cave(entry, addr, bss):
    """fw_movie_update, in FIELD mode with a background movie: never wait,
    and consume a ready frame only when it is due (one movie period after
    the last); otherwise redraw the shown frame. Any other movie: stock
    (ready -> consume, else the stock wait loop)."""
    import ff7nx_audio_cave as AC
    a = AC.Asm(entry, addr)
    AC.mov32(a, 0, GAME_MODE)
    a.emit(A.bl(a.pc(), TRANSLATE))
    a.emit(A.ldrh(8, 0, 0))
    a.emit(A.cmp_imm(8, MODE_FIELD))
    a.bcond('stock', 1)                          # ne: not the field
    AC.mov32(a, 0, BGMOVIE_FLAG)
    a.emit(A.bl(a.pc(), TRANSLATE))
    a.emit(A.ldrb(8, 0, 0))
    a.cbz(8, 'stock')                            # not a background movie
    a.emit(A.ldrb(8, 23, 0))                     # x23 = movie + 8: ready?
    a.emit(_ubfx0(9, 8))
    a.cbz(9, 'ready')                            # not ready: stock redraw
    a.emit(MRS_CNTPCT | 10)                      # now: the hardware counter
    AC.bss_ptr(a, 9, bss)
    a.emit(A.ldr64(11, 9, 0))                    # due (0 = never)
    a.emit(A.sub_reg64(12, 10, 11))              # now - due
    period = CPS // movie_fps()
    AC.mov32(a, 13, 2 * period)
    a.emit(A.cmp_reg64(12, 13))
    a.bcond('resync', 2)                         # hs: stalled, behind, or first
    AC.mov32(a, 13, period - TOLERANCE)
    a.emit(A.cmp_reg64(12, 13))
    a.bcond('skip', 3)                           # lo: not due yet
    AC.mov32(a, 13, period)
    a.emit(A.add_reg64(11, 11, 13))
    a.emit(A.str64(11, 9, 0))                    # due += one period
    a.b('ready')
    a.label('resync')
    a.emit(A.str64(10, 9, 0))                    # due = now
    a.b('ready')
    a.label('skip')
    a.emit(A.ldr(1, 21, 0x14))                   # as +0x10F1658
    a.emit(A.b(a.pc(), NOT_READY_PATH))
    a.label('stock')
    a.emit(A.ldrb(8, 23, 0))
    a.emit(_ubfx0(9, 8))
    a.cbnz(9, 'ready')
    a.emit(A.b(a.pc(), WAIT_LOOP))
    a.label('ready')
    a.emit(A.b(a.pc(), READY_PATH))
    return a.resolve()


def gate_cave(entry, addr):
    """The limiter: field_limit_fps || BGMOVIE_flag."""
    w = []
    pc = lambda: addr(len(w))
    w.append(A.ldr(8, 0, 0))                       # the displaced load
    w.append(A.cbnz(8, pc(), addr(6)))
    w.append(A.movz(0, BGMOVIE_FLAG & 0xFFFF))
    w.append(A.movk_hi(0, BGMOVIE_FLAG >> 16))
    w.append(A.bl(pc(), TRANSLATE))
    w.append(A.ldrb(8, 0, 0))
    w.append(A.b(pc(), GATE_BACK))
    return w


def installed(t):
    return (w32(t, WAIT_HOOK) & 0xFC000000) == 0x14000000


def build_patches(img, starts, bss, log=print):
    import ff7nx_cave
    t = img
    if check_anchors(t, log):
        return None
    pool = ff7nx_cave.HolePool(bytearray(img), starts=starts)
    e1, w1 = ff7nx_cave.emit_laid_out(
        pool, lambda e, ad: wait_cave(e, ad, bss), span=0x80000)
    e2, w2 = ff7nx_cave.emit_laid_out(pool, gate_cave, span=0x80000)
    words = dict(w1)
    words.update(w2)
    words[WAIT_HOOK] = A.b(WAIT_HOOK, e1)
    words[GATE_HOOK] = A.b(GATE_HOOK, e2)
    log('  background-movie 60 fps: movie wait cave +%#x (%d words), limiter '
        'gate cave +%#x (%d words)' % (e1, len(w1), e2, len(w2)))
    return words


def apply(main, log=print):
    import ff7nx_audio_cave as AC
    import nxmap
    main = Path(main)
    m = nxmap.Main(str(main))
    t = m.text
    if installed(t):
        log('  background-movie 60 fps: already installed')
        return 0
    blob = main.read_bytes()
    segs, raw = AC.segments(blob)
    bss = AC.scratch_base(blob, segs)
    words = build_patches(m.img, set(m.arm_starts), bss, log)
    if words is None:
        log('  background-movie 60 fps: anchors do not match -- NOT applied')
        return 1
    text = bytearray(raw[0])
    for va, word in words.items():
        struct.pack_into('<I', text, va, word)
    out = AC.pack(blob, [bytes(text), raw[1], raw[2]],
                  BSS_BYTES + AC.bss_tail_slack(segs))
    fd, tmp = tempfile.mkstemp(dir=str(main.parent), prefix='.moviepace-')
    os.close(fd)
    try:
        Path(tmp).write_bytes(out)
        shutil.move(tmp, str(main))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    log('  background-movie 60 fps (BUILD 618z): installed -- a BGMOVIE field '
        'is paced by the field limiter at 60 and its movie plays at %d fps '
        'by the clock (BSS +%#x). %s=0 disables, %s sets the rate.' % (
            movie_fps(), bss, ENV, FPS_ENV))
    return 0
