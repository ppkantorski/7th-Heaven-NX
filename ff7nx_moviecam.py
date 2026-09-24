#!/usr/bin/env python3
r"""
ff7nx_moviecam.py -- the movie camera at 30 fps, interpolated, not repeated.

WHAT WAS WRONG
==============
With 30 FPS FMV support on, `build._emplace_moviecam` stretches every track in
`moviecam.lgp` so that it has one record per 30 fps frame. It did that by
writing each 15 fps record TWICE:

    r0 r0 r1 r1 r2 r2 ...

That fixed the budget, the index and the length -- all three land with the
movie again -- but the camera itself still moves on the 15 Hz beat. On a
movie with real 30 fps motion (Cosmos FMV 30), the background moves every
frame and the camera that projects the field models over it moves every
OTHER frame. So every model composited over the movie slides in steps
against a background that does not. That is the choppiness.

It is the same shape as the summon camera before build 384: the logic ran at
a lower rate than the picture, and the hold that fixed the strobe left the
step. FFNx's answer there was interpolation; this is the same answer for a
different camera.

WHAT THIS DOES
==============
Writes the in-between record instead of a copy:

    r0  mid(r0,r1)  r1  mid(r1,r2)  r2 ...

Every EVEN record is still the original, byte for byte, at exactly the frame
vanilla would show it -- so the track, the budget (`size/4 - 2`, spent ten
dwords a call), the index and the tail are identical to the duplicating
stretch. Only the odd records change. It is data, not a cave: `exefs/main`
is untouched and nothing about how the game reads the file is different.

THE RECORD (40 bytes, the same layout as a field's section-2 camera)
====================================================================
MEASURED over all 124 tracks, 12409 consecutive pairs, 0 exceptions to:

    +0x00  s16[3]  axis X  |  unit vectors in 4096ths --
    +0x06  s16[3]  axis Y  |  every valid record has all three norms
    +0x0C  s16[3]  axis Z  |  within a few units of 4096
    +0x12  s16     axis Z.z again (always equal to +0x10)
    +0x14  s32[3]  position (|x| <= 40196 on every valid record)
    +0x20  s16[2]  two parameters that ramp smoothly (steps of 1..5)
    +0x24  u16     zoom (continuous steps are <= 32)
    +0x26  u16     a 0/1 flag

A record with the sentinel position 0x7FFFFFFF / zoom 0xFFFF (hwindfly,
zmind31, uropein) or an all-zero body is not a camera, and is never blended.

HARD CUTS STAY HARD
===================
Blending across a cut would show, for one 30th of a second, a camera halfway
between two unrelated shots -- a visible glitch frame, which is worse than
the step this removes. So the costs are asymmetric and the rule leans toward
calling a cut: a pair is only blended when EVERY test says it is continuous.

The thresholds come from the data, not from a guess. Across every valid pair:

    rotation per step   11325 pairs under 3 deg (6503 of them exactly 0),
                        72 between 3 and 8 deg (the fastest real pans:
                        gold3, jairofly, cscene1), then NOTHING between 8.0
                        and 16.6 deg, where the cuts start. Threshold: 12
                        deg, in the middle of the empty band. (Measured with
                        atan2 -- see `_angle` for why acos lied.)
    zoom per step       continuous steps top out at 32; the next value up is
                        80. Threshold: 48.
    position per step   continuous pans reach ~1500 units a step (opening,
                        gelnica, gold3) -- so an absolute limit alone would
                        either call those cuts or miss real ones. A cut is a
                        SPIKE: a step several times the size of the steps
                        around it. Rule: over 5000 always, or over 600 and
                        more than 3x the smaller neighbouring step.
                        Continuous pans measure 0.8 .. 1.2x their
                        neighbours; the smallest real cut measures 19.7x.
    the ramped pair     continuous steps are 1..5; over 64 is a cut.
    the flag            any change is a cut.

Taking the SMALLER neighbour, not the larger, is deliberate: gold7 has two
cuts back to back (records 279 and 280). Against the larger neighbour each
looks like 1x the other and both would be blended; against the smaller each
is a spike.

A misjudged cut costs one held record -- exactly what shipped before this.
A misjudged blend costs a glitch frame. That is why every doubt resolves to
the hold.

WHICH MOVIES
============
Only a movie whose SOURCE really has 30 fps motion benefits. A movie the
build frame-doubles from the game's own 15 fps copy shows each picture twice,
and a camera moving between those pictures would make the models swim
against a background that is standing still. So interpolation is per track,
decided by the source the build is actually going to use -- see
`build._moviecam_true_rate_stems`. Everything else keeps the duplicating
stretch, byte for byte.
"""
from __future__ import annotations

import math
import struct

REC = 40
FMT = '<9hh3i2hHH'
assert struct.calcsize(FMT) == REC

UNIT = 4096
UNIT_SLACK = 64              # measured norms are 4093..4099
POS_LIMIT = 1 << 20          # valid |pos| <= 40196; the sentinel is 0x7FFFFFFF
ZOOM_SENTINEL = 0xFFFF

CUT_DEGREES = 12.0
CUT_ZOOM = 48
CUT_POS_ABS = 5000.0
CUT_POS_MIN = 600.0
CUT_POS_RATIO = 3.0
CUT_RAMP = 64


def decode(rec):
    s = struct.unpack(FMT, rec)
    return {
        'axes': [list(s[0:3]), list(s[3:6]), list(s[6:9])],
        'zdup': s[9],
        'pos': list(s[10:13]),
        'ramp': [s[13], s[14]],
        'zoom': s[15],
        'flag': s[16],
    }


def encode(r):
    a = r['axes']
    return struct.pack(FMT, *a[0], *a[1], *a[2], r['zdup'], *r['pos'],
                       *r['ramp'], r['zoom'], r['flag'])


def valid(rec):
    """A real camera, not a gap, a sentinel or a half-written record."""
    if not any(rec[:REC - 2]):
        return False
    r = decode(rec)
    if r['zoom'] == ZOOM_SENTINEL:
        return False
    if any(abs(p) >= POS_LIMIT for p in r['pos']):
        return False
    for v in r['axes']:
        if abs(math.sqrt(sum(c * c for c in v)) - UNIT) > UNIT_SLACK:
            return False
    return True


def _angle(a, b):
    """Largest angle, in degrees, between corresponding axes of two records.

    atan2(|u x v|, u . v), NOT acos(u . v / 4096^2). The stored axes are
    rounded to whole 4096ths, so their norms are 4093..4099, and acos near 1
    is so ill-conditioned that a one-unit norm error reads as ~1.3 degrees --
    two IDENTICAL records measured 1.79 degrees apart that way. The first
    survey of this archive used acos, and its "median step 1.5 degrees" was
    almost entirely that noise. atan2 is well conditioned at every angle and
    independent of the norms.
    """
    worst = 0.0
    for u, v in zip(a['axes'], b['axes']):
        cx = u[1] * v[2] - u[2] * v[1]
        cy = u[2] * v[0] - u[0] * v[2]
        cz = u[0] * v[1] - u[1] * v[0]
        dot = sum(x * y for x, y in zip(u, v))
        worst = max(worst, math.degrees(
            math.atan2(math.sqrt(cx * cx + cy * cy + cz * cz), dot)))
    return worst


def _dpos(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a['pos'], b['pos'])))


def blendable(recs, k):
    """May records k and k+1 be blended? Every doubt answers no."""
    if k < 0 or k + 1 >= len(recs):
        return False
    if not (valid(recs[k]) and valid(recs[k + 1])):
        return False
    a, b = decode(recs[k]), decode(recs[k + 1])
    if a['flag'] != b['flag']:
        return False
    if abs(a['zoom'] - b['zoom']) > CUT_ZOOM:
        return False
    if any(abs(x - y) > CUT_RAMP for x, y in zip(a['ramp'], b['ramp'])):
        return False
    if _angle(a, b) > CUT_DEGREES:
        return False
    d = _dpos(a, b)
    if d > CUT_POS_ABS:
        return False
    if d > CUT_POS_MIN:
        around = []
        for j in (k - 1, k + 1):
            if 0 <= j and j + 1 < len(recs) and valid(recs[j]) \
                    and valid(recs[j + 1]):
                around.append(_dpos(decode(recs[j]), decode(recs[j + 1])))
            else:
                around.append(0.0)       # no neighbour = a spike by default
        if d > CUT_POS_RATIO * max(min(around), 1.0):
            return False
    return True


def _s16(x):
    return max(-32768, min(32767, int(round(x))))


def blend(ra, rb, t):
    """The record `t` of the way from `ra` to `rb` (0 < t < 1)."""
    a, b = decode(ra), decode(rb)
    axes = []
    for u, v in zip(a['axes'], b['axes']):
        w = [x + (y - x) * t for x, y in zip(u, v)]
        n = math.sqrt(sum(c * c for c in w)) or 1.0
        # Renormalise: the chord between two unit vectors is shorter than a
        # unit vector (cos 6 deg = 0.9945 at the 12 deg limit). Left alone
        # that is a 0.5% zoom-in on every in-between frame -- a shimmer.
        axes.append([_s16(c * UNIT / n) for c in w])
    return encode({
        'axes': axes,
        'zdup': axes[2][2],                   # the invariant the data keeps
        'pos': [int(round(x + (y - x) * t)) for x, y in zip(a['pos'], b['pos'])],
        'ramp': [_s16(x + (y - x) * t) for x, y in zip(a['ramp'], b['ramp'])],
        'zoom': max(0, min(0xFFFE, int(round(a['zoom'] + (b['zoom'] - a['zoom']) * t)))),
        'flag': a['flag'],
    })


def stretch_repeat(payload, ratio, tail):
    """The shipped stretch: every record `ratio` times, then a held tail."""
    if not payload or len(payload) % REC:
        return payload * ratio            # not camera data; nothing indexes it
    body = b''.join(payload[i:i + REC] * ratio
                    for i in range(0, len(payload), REC))
    return body + payload[-REC:] * (tail * ratio)


def stretch_interpolated(payload, ratio, tail):
    """
    Same length, same even records, same tail as `stretch_repeat` -- with the
    in-between records blended wherever the pair is continuous.

    Returns (bytes, blended, held): how many in-between records were blended
    and how many were held because the pair is a cut or not a camera.
    """
    if not payload or len(payload) % REC:
        return payload * ratio, 0, 0
    recs = [payload[i:i + REC] for i in range(0, len(payload), REC)]
    out, blended, held = [], 0, 0
    for k, r in enumerate(recs):
        out.append(r)
        ok = blendable(recs, k)
        for j in range(1, ratio):
            if ok:
                out.append(blend(r, recs[k + 1], j / float(ratio)))
                blended += 1
            else:
                out.append(r)
                if k + 1 < len(recs) and any(r[:REC - 2]):
                    held += 1
    return b''.join(out) + payload[-REC:] * (tail * ratio), blended, held
