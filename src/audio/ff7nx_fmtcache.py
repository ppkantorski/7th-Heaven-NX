#!/usr/bin/env python3
"""
ff7nx_fmtcache.py -- BUILD 813. audio.fmt, and audio.dat's world-footstep
bank, are read from memory, not storage.

WHAT WAS MEASURED
=================
PROBE-812 (wmfpsprobe v1..v3, the world map right after loading a save; the
frame rate "unstable as I move around, it eventually fixes itself"): about
every 20 frames one frame took ~70 ms against ~14.5 ms. No texture loads, the
present ~0.5 ms: game time. Exactly one main-thread file read sat in each of
those frames and took ~55 ms; ordinary frames read nothing. The slowest read
was 24 bytes of

    rom://ff7/workingdir/data/sound/audio.fmt

-- one sound format record. The stock `sfx_load` reads a row's record from
audio.fmt each time it loads a row that is not in the sound cache, and
ff7nx_worldsteps plays every world-map footstep on reserved row 744, which is
never cached by design (0.30 s cadence = the 20-frame spacing). audio.dat
itself is already memory-backed by the port (+0x1118EF0); audio.fmt was not.
The read gets cheaper once storage has it cached, hence "fixes itself".

WHAT THIS DOES
==============
The port's stream read (+0x1119350, read(stream, buf, size); the stream's
path at +8, position at +280, size at +288) is entered through a cave that
calls native/fmtcache.c `fc_read`. For a stream whose path ends in
"sound/audio.fmt" the whole file (18 000 bytes: 750 x 24) is read once with
the stock read and kept in BSS; every read after that is a copy with the
stock read's position update and return value. Any other file, or a failed
fill, takes the stock read unchanged (fc_read returns -1). audio.fmt is never
written while the game runs.

813b. With audio.fmt cached the hitch halved but stayed: wmfpsprobe v6 found
the step's other read, 17 KB of audio.dat at 0x64C4A4D (~50 ms) -- the
padded footstep variant itself, in the bank the build appends at
plan.world_footsteps['payload_base'] (68 variants, ~1.2 MB). So the build
now also passes that range: on the first read of audio.dat at all (boot)
the whole bank is read once into BSS, and a read lying entirely inside it is
a copy. Reads outside it are the stock read. A battle reads near the bank,
which is why the hitches stopped after the first battle.

SEVENTH_NX_FMT_CACHE=0 leaves the read as it was.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import os
import struct

import nxpaths
import a64 as A
import ff7nx_audio_cave as AC
import ff7nx_deadspace as DS

ENV = 'SEVENTH_NX_FMT_CACHE'
BLOB = os.path.join(nxpaths.APP, 'native', 'fmtcache.bin')
READ = 0x1119350
READ_WORD = 0xF81C0FF7            # str x23, [sp, #-64]!
CAP = 65536
HEADER = 32                       # fmtcache.c State before buf
MAX_RANGE = 4 << 20               # never hold more than 4 MB of audio.dat


def state_bytes(range_len):
    return HEADER + CAP + range_len


def enabled(env=None):
    env = os.environ if env is None else env
    return env.get(ENV, '1').strip().lower() not in ('0', 'off', 'no', 'false')


def _addr(w, reg, at, value):
    pc = at + 4 * len(w)
    w.append(A.adrp(reg, pc, value & ~0xFFF))
    w.append(A.add_imm64(reg, reg, value & 0xFFF))


def read_cave(at, state, fn, table):
    """fc_read(state, stream, buf, size, stock, table); -1 -> the stock read.
    `stock` is this cave's trampoline: the displaced word, then READ+4;
    `table` the {offset, length} of the audio.dat range (0, 0 = none)."""
    w = [A.stp64_pre(29, 30, 31, -0x30),
         A.stp64_off(0, 1, 31, 0x10),
         A.str64(2, 31, 0x20),
         A.mov_reg64(3, 2), A.mov_reg64(2, 1), A.mov_reg64(1, 0)]
    _addr(w, 0, at, state)
    _addr(w, 5, at, table)
    tramp_at = len(w)
    w += [0, 0]                            # adr x4, tramp (patched below)
    w.append(0)                            # bl fc_read
    w.append(A.add_imm64(9, 0, 1))         # x9 = result + 1 (x9 is dead here)
    fall = len(w)
    w.append(0)                            # cbz x9, fall
    w += [A.ldp64_post(29, 30, 31, 0x30), A.ret()]
    f_at = len(w)
    w += [A.ldp64_off(0, 1, 31, 0x10), A.ldr64(2, 31, 0x20),
          A.ldp64_post(29, 30, 31, 0x30)]
    t_at = len(w)
    w += [READ_WORD, 0]
    tramp = at + 4 * t_at
    tmp = []
    _addr(tmp, 4, at + 4 * tramp_at, tramp)
    w[tramp_at:tramp_at + 2] = [tmp[0], tmp[1]]
    w[tramp_at + 2] = A.bl(at + 4 * (tramp_at + 2), fn)
    w[fall] = A.cbz64(9, at + 4 * fall, at + 4 * f_at)
    w[t_at + 1] = A.b(at + 4 * (t_at + 1), READ + 4)
    return w


def check(text):
    got = struct.unpack_from('<I', text, READ)[0]
    if got != READ_WORD:
        raise ValueError('fmt cache: +0x%X is %08X, not %08X (stream read entry)'
                         % (READ, got, READ_WORD))


def world_steps_range(plan):
    """(offset, length) of the world-footstep bank in the built audio.dat, or
    (0, 0) when this build has none / it is too large to hold."""
    ws = getattr(plan, 'world_footsteps', None) if plan is not None else None
    if not ws or ws.get('payload_base') is None or not ws.get('payloads'):
        return 0, 0
    n = sum(len(p) for p in ws['payloads'])
    if not n or n > MAX_RANGE:
        return 0, 0
    return int(ws['payload_base']), n


def apply_to_nso(src, dest, stock=None, log=lambda *_: None, region=None,
                 dat_range=(0, 0)):
    blob = open(src, 'rb').read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    check(text)
    lo, hi = region if region else DS.part(src, 'fmt', stock)
    base = AC.scratch_base(blob, segs)
    state = (base + 15) & ~15
    d_lo, d_len = dat_range
    if d_len > MAX_RANGE:
        raise ValueError('fmt cache: an audio.dat range of %d bytes is too large' % d_len)
    growth = (state - base) + state_bytes(d_len) + AC.bss_tail_slack(segs)
    code = open(BLOB, 'rb').read()
    c_at = (lo + 15) & ~15
    t_at = (c_at + len(code) + 15) & ~15
    table = struct.pack('<QQ', d_lo, d_len)
    r_at = t_at + 16
    rw = read_cave(r_at, state, c_at, t_at)
    end = r_at + 4 * len(rw)
    if end > hi:
        raise ValueError('fmt cache: %d bytes do not fit the %d-byte part'
                         % (end - lo, hi - lo))
    text[c_at:c_at + len(code)] = code
    text[t_at:t_at + 16] = table
    for i, x in enumerate(rw):
        struct.pack_into('<I', text, r_at + 4 * i, x)
    struct.pack_into('<I', text, READ, A.b(READ, r_at))
    raw[0] = bytes(text)
    out = AC.pack(blob, raw, growth)
    _s, craw = AC.segments(out)
    if craw[0][c_at:c_at + len(code)] != code:
        raise ValueError('fmt cache: the blob did not survive the repack')
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(out)
    log('  audio.fmt cache: sound format records read from memory after the '
        'first read%s; code +0x%X, cave +0x%X, state +0x%X (%d KB)'
        % ((', and audio.dat 0x%X..0x%X (the world-footstep bank) from boot'
            % (d_lo, d_lo + d_len)) if d_len else '',
           c_at, r_at, state, state_bytes(d_len) // 1024))
    return {'code': c_at, 'table': t_at, 'cave': r_at, 'state': state, 'bss': growth,
            'bytes': end - lo}
