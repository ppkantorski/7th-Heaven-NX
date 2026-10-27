#!/usr/bin/env python3
"""
ff7nx_wmtime.py -- BUILD 649. The world map's own night and sunset looks,
on the day/night clock.

WHAT THE GAME HAS
=================
The world map keeps 16 colour zones (guest 0xE28CE0, 0x24 bytes each) that
wm0.ev fills with opcodes 0x310..0x316. Each frame, x86 0x7550A7 (called
from 0x74DF86; ARM bl at +0xF2A30C) blends every active zone whose radius
reaches the player into light 0 and the sky quad (top and horizon colours),
by distance; outside them all it restores the defaults. Two of them are the
looks this build borrows:
  zone 0, Midgar         light (60,90,100)  sky (0,0,65) -> (145,255,190):
                         the dark night with the bright yellow meteor
  zone 2, Cosmo Canyon   light (255,130,40) sky (130,120,180) -> (240,100,50):
                         the red sunset
They only show near those towns.

WHAT THIS DOES
==============
While the map is the overworld (map type 0) -- the build installs this
only with the day/night cycle, and BUILD 719 no longer asks the savemap's
options bit as well (see below) --
native/wmtime.c runs around that call: before it, every scripted zone is
muted and zone 15 holds the clock's mix of default, night and sunset at a
radius covering the planet; after it, every byte of the table is put back
(scripts and other readers never see the change). The game's own blend,
light and sky code do the rest, under the existing day/night tint:
  20:00-06:00  night (Midgar's look), full
  06:00-07:00  night hands over to the sunrise (Cosmo's look)
  07:00-10:00  the sunrise fades to the normal day
  17:00-19:00  the sunset (Cosmo's look) comes in
  19:00-20:00  the sunset hands over to the night
Smoothstep ramps, one crossfade per hand-over. The sky takes the towns'
colours in full; the light (ground and models) takes 55% of Midgar's and 50%
of Cosmo's, because the day/night tint darkens it as well and the two in
full crush the ground to near black (PREVIEW-649). Towns no longer force their
own look while the clock is on (Midgar is night at noon otherwise).
Underwater, snow field, or no day/night cycle in the build: nothing changes.

BUILD 719: the savemap's options bit 0 is no longer a condition. The cycle
arms itself (ff7nx_daynight force_on ORs the bit into a register) and only
the field tick writes the byte back, so a save loaded on the world map with
the bit clear had the tint but none of this -- the stock blue sky, Midgar
darkening and Cosmo Canyon going red as you neared them -- until entering a
town or the Highwind wrote the bit.

Installed by build.apply_daynight after the cycle itself; fails closed.
SEVENTH_NX_WMTIME=0 skips it.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import nxpaths  # BUILD 755: project paths (src/nxpaths.py)
import os
import struct

import a64 as A
import ff7nx_audio_cave as AC
import ff7nx_deadspace as DS

HERE = nxpaths.ROOT
BLOB = os.path.join(nxpaths.APP, 'native', 'wmtime.bin')
ENTRY = os.path.join(nxpaths.APP, 'native', 'wmtime.entry')
ENV = 'SEVENTH_NX_WMTIME'

CALL = 0xF2A30C                 # x86 0x74DF86: call 0x7550A7 (zone blend)
TARGET = 0xF33FA0               # ARM body of 0x7550A7
GUEST_PAGES_GOT = 0x12CE8C0
STATE_BYTES = 0x60              # sizeof(State) <= 0x60 (static assert in C)

ZONES = 0xE28CE0
ZONE_BYTES = 0x24
MAP_TYPE = 0xE045E8
OPTIONS = 0xDC08E6             # not read since BUILD 719 (see above)
MINUTES = 0xDC08E8
HOURS = 0xDC08EB

# the looks, as wmtime.c builds them: light, sky top, sky horizon
DEFAULT = ((253, 253, 253), (0, 0, 255), (250, 255, 255))
# wm0.ev zone 0's light; its sky (0,0,65 -> 145,255,190) darkened (649b)
NIGHT = ((60, 90, 100), (0, 0, 28), (60, 100, 95))
SUNSET = ((255, 130, 40), (130, 120, 180), (240, 100, 50))  # wm0.ev zone 2
LIGHT_NIGHT = 140               # of 256: the towns' light the clock takes
LIGHT_SUN = 128                 # (the sky takes all of it; see wmtime.c)


def enabled(env=None):
    v = (os.environ if env is None else env).get(ENV, '1').strip().lower()
    return v not in ('0', 'off', 'false', 'no')


def entries():
    out = {}
    for line in open(ENTRY):
        name, off = line.split()
        out[name] = int(off, 16)
    return out


def ramp(t, a, b):
    """wmtime.c's 0..256 smoothstep."""
    if t <= a:
        return 0
    if t >= b:
        return 256
    x = ((t - a) << 8) // (b - a)
    return (x * x * (768 - 2 * x)) >> 16


def weights(minute):
    """(night, sunset), 0..256 each, as wmtime.c computes them."""
    m = minute
    if m < 360 or m >= 1200:
        return 256, 0
    if m < 420:
        s = ramp(m, 360, 420)
        return 256 - s, s
    if m < 1020:
        return 0, 256 - ramp(m, 420, 600)
    if m < 1140:
        return 0, ramp(m, 1020, 1140)
    n = ramp(m, 1140, 1200)
    return n, 256 - n


def look(minute):
    """The zone colours wmtime.c writes at this minute (light, top, horizon),
    or None for the plain default."""
    n, s = weights(minute)
    if n + s == 0:
        return None
    out = []
    for k in range(3):
        a, b = (((n * LIGHT_NIGHT) >> 8, (s * LIGHT_SUN) >> 8) if k == 0
                else (n, s))
        out.append(tuple((d * (256 - a - b) + nv * a + sv * b) >> 8
                         for d, nv, sv in zip(DEFAULT[k], NIGHT[k], SUNSET[k])))
    return tuple(out)


def _addr(w, reg, at, value):
    w.append(A.adrp(reg, at + 4 * len(w), value & ~0xFFF))
    w.append(A.add_imm64(reg, reg, value & 0xFFF))


def _got(w, reg, at, got):
    w.append(A.adrp(reg, at + 4 * len(w), got & ~0xFFF))
    w.append(A.ldr64(reg, reg, got & 0xFFF))


def stub(at, state, fn):
    """bl here instead of 0x74DF86's call to 0x7550A7 (x86 convention: the
    argument and return slot are on the guest stack, which neither C call
    touches): wt_pre(state, pages), the call, wt_post(state)."""
    w = [A.stp64_pre(29, 30, 31, -0x10)]
    _addr(w, 0, at, state)
    _got(w, 1, at, GUEST_PAGES_GOT)
    w.append(A.bl(at + 4 * len(w), fn['wt_pre']))
    w.append(A.bl(at + 4 * len(w), TARGET))
    _addr(w, 0, at, state)
    w.append(A.bl(at + 4 * len(w), fn['wt_post']))
    w += [A.ldp64_post(29, 30, 31, 0x10), A.ret()]
    return w


def check(text):
    got = struct.unpack_from('<I', text, CALL)[0]
    if got != A.bl(CALL, TARGET):
        raise ValueError('wmtime: +0x%X is %08X, not bl +0x%X (zone blend)'
                         % (CALL, got, TARGET))


def apply_to_nso(src, dest, stock=None, log=lambda *_: None):
    blob = open(src, 'rb').read()
    segs, raw = AC.segments(blob)
    text = bytearray(raw[0])
    check(text)
    lo, hi = DS.part(src, 'wmtime', stock)
    code = open(BLOB, 'rb').read()
    ent = entries()
    base = AC.scratch_base(blob, segs)
    state = (base + 15) & ~15
    growth = (state - base) + STATE_BYTES + AC.bss_tail_slack(segs)
    entry = (lo + 15) & ~15
    fn = {k: entry + v for k, v in ent.items()}
    at = (entry + len(code) + 15) & ~15
    words = stub(at, state, fn)
    end = at + 4 * len(words)
    if end > hi:
        raise ValueError('wmtime: %d bytes do not fit the %d-byte part'
                         % (end - lo, hi - lo))
    text[entry:entry + len(code)] = code
    for i, x in enumerate(words):
        struct.pack_into('<I', text, at + 4 * i, x)
    struct.pack_into('<I', text, CALL, A.bl(CALL, at))
    raw[0] = bytes(text)
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(AC.pack(blob, raw, growth))
    log('  world map looks: Midgar\'s night and Cosmo Canyon\'s sunset/sunrise '
        'follow the clock everywhere on the overworld (towns no longer force '
        'theirs while it runs); code +0x%X (%d bytes), state +0x%X'
        % (entry, end - lo, state))
    return {'code': entry, 'stub': at, 'bytes': end - lo, 'state': state,
            'bss': growth, 'entries': fn}
