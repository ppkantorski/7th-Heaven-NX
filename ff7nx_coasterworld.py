#!/usr/bin/env python3
r"""
ff7nx_coasterworld.py -- the Shooting Coaster's world, widened for 16:9.
coaster.lgp DATA ONLY (xbin.bin, rewritten in place, same size). No code.

    SEVENTH_NX_COASTER_WIDEN=L,R    add L steps earlier, remove R steps later
                                    (default 1,2; 0,0 = stock bytes)
    SEVENTH_NX_COASTER_FAR=F        draw distance x F (default 1.25, 1..1.5;
                                    1 = stock) -- BUILD 564, with ff7nx_coaster

WHY THINGS VANISH AT THE 16:9 EDGES
===================================
The coaster world is not culled at run time. It is a precomputed visibility
stream: per TRACK STEP (car position >> 18) the ride file lists which
polygons to ADD to the draw lists and which to REMOVE (xbin.bin; offsets in
xbinadr.bin, entries 8/9 for the triangle list 0xC503B0, 2/3 for the quad
list 0xC476F0; read by x86 0x5EDC59 and 0x5EDD82 one step at a time). Those
sets were computed for the 4:3 frame. A polygon leaving the 4:3 frame is
REMOVED -- in 16:9 it is still on screen, at the left or right edge, and
disappears there. The same stream also removes and re-adds a polygon one or
two steps apart as it leaves and re-enters the 4:3 view (1,024 such pairs in
the triangle stream at a gap of one step), which is flicker at the edges.

WHY NOT SIMPLY DELAY THE REMOVALS IN CODE
=========================================
Measured (see tests/test_coaster.py): delaying removal by K steps re-adds
thousands of polygons that are still linked -- the lists are intrusive
doubly-linked lists keyed by polygon index, and a second insert of a linked
index corrupts them. Presence has to be the UNION of the stretched intervals,
which is a property of the data, so it is fixed in the data.

THE REWRITE
===========
Each polygon's stock stream is a clean sequence of [add, remove) intervals
(checked: never two adds or two removes in a row, never an add and a remove
of one index in one step). Every interval becomes [add - L, remove + R),
overlapping ones merge, and the four streams are re-emitted with the same
2,141 steps. Merging only ever removes entries, so every stream fits in its
own region and xbinadr.bin is untouched; the freed tail of a region is filled
with 0xFFFF -- empty steps past the last one, never a stale list. Within a step the
stock order is kept (new add lists are ordered by original step, then
original position), so draw order only moves with the time shift itself.

Cost, measured on the stream: peak simultaneous polygons 1,157 -> 1,478
triangles and 210 -> 246 quads at 1,2. The draw lists are indexed, not
pooled (every index has its own slot), and the deferred draw reserves its
vertices per quad (0x66E272), so the peak only costs draw time.

DRAW DISTANCE (BUILD 564)
=========================
Stock, the ride ends at 14300 units three ways at once: the projection far
plane (game_obj+0x9A4, x86 0x5E8B12), the fog that fades to black (start
10410 / end 14300, [0xC3F750]/[0xC3F754], x86 0x5E924F/0x5E9259) and the
streams, which add a polygon when it comes within ~14000 of the car
(measured from the rails: p90 13934, max 14000; a track step is 137
units). Raising only one of them shows nothing new. F scales all three:
ff7nx_coaster moves the far plane and both fog distances (exefs/main), and
here every add that happens FAR from the car (> 12000 at the add step --
the draw-distance adds, not the near visibility flicker) moves LF =
round((F - 1) * 14300 / 137) steps earlier, so the polygon exists as it
crosses the new far limit. Near adds keep L. At F = 1.25 (LF = 26): mean
polygons drawn 487 -> 686 triangles, 75 -> 106 quads; peak 1478 -> 1801.
"""
from __future__ import annotations

import os
import struct

ENV = 'SEVENTH_NX_COASTER_WIDEN'
FAR_ENV = 'SEVENTH_NX_COASTER_FAR'
FAR_DEFAULT = 1.25
STOCK_FAR = 14300.0
STEP_UNITS = 137.0
FAR_ADD_DIST = 12000.0
DEFAULT = (1, 2)
VERSION = 'coasterworld-564'
XBIN, XBINADR = 'xbin.bin', 'xbinadr.bin'
PSX_BASE = 0x800F0000
STREAMS = ((8, 9), (2, 3))            # (add, remove) table entries


def widen(env=None):
    raw = (os.environ if env is None else env).get(ENV, '').strip()
    if not raw:
        return DEFAULT
    if raw.lower() in ('0', 'off', 'stock', 'no'):
        return (0, 0)
    try:
        l_, r_ = (int(x) for x in raw.split(','))
    except ValueError:
        return DEFAULT
    if not (0 <= l_ <= 8 and 0 <= r_ <= 8):
        return DEFAULT
    return (l_, r_)


def far_factor(env=None):
    raw = (os.environ if env is None else env).get(FAR_ENV, '').strip()
    try:
        v = float(raw) if raw else FAR_DEFAULT
    except ValueError:
        v = FAR_DEFAULT
    return v if 1.0 <= v <= 1.5 else FAR_DEFAULT


def far_steps(f):
    return int(round((f - 1.0) * STOCK_FAR / STEP_UNITS))


def _table(adr):
    n = len(adr) // 4
    return [x - PSX_BASE for x in struct.unpack('<%dI' % n, adr[:4 * n])]


def _region(table, size, k):
    off = table[k]
    ends = [t for t in table if t > off] + [size]
    return off, min(ends)


def read_stream(xbin, off, end):
    steps, cur = [], []
    for p in range(off, end - 1, 2):
        v = struct.unpack_from('<H', xbin, p)[0]
        if v == 0xFFFF:
            steps.append(cur)
            cur = []
        else:
            cur.append(v)
    return steps


def _intervals(adds, rems):
    ev = {}
    for s, lst in enumerate(adds):
        for pos, i in enumerate(lst):
            ev.setdefault(i, []).append((s, 1, pos))
    for s, lst in enumerate(rems):
        for pos, i in enumerate(lst):
            ev.setdefault(i, []).append((s, 0, pos))
    out = {}
    for i, e in ev.items():
        e.sort(key=lambda t: (t[0], -t[1]))
        cur = None
        lst = []
        for s, kind, pos in e:
            if kind == 1:
                if cur is not None:
                    raise ValueError('index %d added twice (step %d)' % (i, s))
                cur = (s, pos)
            else:
                if cur is None:
                    raise ValueError('index %d removed while absent (step %d)'
                                     % (i, s))
                if s == cur[0]:
                    raise ValueError('index %d added and removed in step %d'
                                     % (i, s))
                lst.append((cur[0], cur[1], s))
                cur = None
        if cur is not None:
            lst.append((cur[0], cur[1], None))          # never removed
        out[i] = lst
    return out


class _Geo:
    """Rails (xbinadr 4 via the offsets at 5) and triangle vertices (10):
    the distance from the car at a step to a polygon of either list."""

    def __init__(self, xbin, table):
        import math
        self.math = math
        self.x = xbin
        self.t = table
        self.o0, self.o1 = struct.unpack_from('<2I', xbin, table[5])

    def rail(self, base, i):
        return struct.unpack_from('<3h', self.x, self.t[4] + base + 8 * i)

    def car(self, step):
        a, b = self.rail(self.o0, 4 * step), self.rail(self.o1, 4 * step)
        return [(a[k] + b[k]) / 2.0 for k in range(3)]

    def tri(self, step, i):
        c = self.car(step)
        return min(self.math.dist(c, struct.unpack_from(
            '<3h', self.x, self.t[10] + 0x24 * i + 8 * k)) for k in range(3))

    def quad(self, step, i):
        c = self.car(step)
        return min(self.math.dist(c, self.rail(self.o0, i)),
                   self.math.dist(c, self.rail(self.o1, i)))


def dilate(adds, rems, left, right, far_left=0, far_of=None):
    """New (adds, rems) step lists, same number of steps."""
    n = len(adds)
    if len(rems) != n:
        raise ValueError('add/remove streams differ in length')
    iv = _intervals(adds, rems)
    new_add = [[] for _ in range(n)]
    new_rem = [[] for _ in range(n)]
    inf = n + 1
    for i, lst in iv.items():
        spans = []
        for a, pos, r in lst:
            lead = left
            if far_left > left and far_of is not None and a > 0 \
                    and far_of(a, i) > FAR_ADD_DIST:
                lead = far_left
            a2 = max(0, a - lead)
            r2 = inf if r is None or r + right >= n else r + right
            spans.append((a2, a, pos, r2))
        spans.sort()
        merged = []
        for a2, a, pos, r2 in spans:
            if merged and a2 <= merged[-1][3]:
                m = merged[-1]
                merged[-1] = (m[0], m[1], m[2], max(m[3], r2))
            else:
                merged.append((a2, a, pos, r2))
        merged = [(a2, a, pos, None if r2 == inf else r2)
                  for a2, a, pos, r2 in merged]
        for a2, a, pos, r2 in merged:
            new_add[a2].append((a, pos, i))
            if r2 is not None:
                new_rem[r2].append((r2, len(new_rem[r2]), i))
    adds_out = [[i for _a, _p, i in sorted(step)] for step in new_add]
    # removals: stock order for those that did not move, then the moved ones
    rems_out = [[i for _a, _p, i in step] for step in new_rem]
    return adds_out, rems_out


def _emit(steps):
    out = bytearray()
    for lst in steps:
        for i in lst:
            out += struct.pack('<H', i)
        out += b'\xff\xff'
    return bytes(out)


def rewrite(xbin, adr, left, right, far_left=0):
    """xbin.bin bytes with the four visibility streams widened in place."""
    if (left, right) == (0, 0) and far_left == 0:
        return bytes(xbin)
    table = _table(adr)
    geo = _Geo(xbin, table) if far_left else None
    out = bytearray(xbin)
    for (k_add, k_rem), kind in zip(STREAMS, ('tri', 'quad')):
        a_off, a_end = _region(table, len(xbin), k_add)
        r_off, r_end = _region(table, len(xbin), k_rem)
        adds = read_stream(xbin, a_off, a_end)
        rems = read_stream(xbin, r_off, r_end)
        na, nr = dilate(adds, rems, left, right, far_left,
                        getattr(geo, kind) if geo else None)
        for off, end, steps in ((a_off, a_end, na), (r_off, r_end, nr)):
            blob = _emit(steps)
            if off + len(blob) > end:
                raise ValueError('widened stream at 0x%X does not fit (%d > %d)'
                                 % (off, len(blob), end - off))
            out[off:off + len(blob)] = blob
            # past the last step: empty steps (0xFFFF), never a stale list
            out[off + len(blob):end] = b'\xff' * (end - off - len(blob))
    return bytes(out)


def peaks(xbin, adr, steps=None):
    """Peak simultaneous (triangles, quads) the game draws, stepping exactly
    as 0x5EDC59 / 0x5EDD82 do (all adds of a step, then its removals; the
    peak is taken after both, which is what a frame draws)."""
    table = _table(adr)
    res = []
    for k_add, k_rem in STREAMS:
        a = read_stream(xbin, *_region(table, len(xbin), k_add))
        r = read_stream(xbin, *_region(table, len(xbin), k_rem))
        n = min(len(a), len(r)) if steps is None else steps
        live = set()
        peak = 0
        for s in range(n):
            live.update(a[s])
            live.difference_update(r[s])
            peak = max(peak, len(live))
        res.append(peak)
    return tuple(res)
