#!/usr/bin/env python3
"""
ff7nx_deadspace.py -- code space borrowed from a translated function that is
PROVEN dead, shared between the passes that need it.

The padding pool is spent by the late module passes (BUILD 532 measured it:
no 1 MB window has room for a windowed cave on a finished module). x86
0x623D28, the 25 KB PSX tile rasteriser, is reached only from 0x620BD3, which
nothing reaches -- `ff7nx_fieldbg._liveness` proves both on every build. Its
ARM body is 72 KB.

It is split so a shipping pass and the diagnostic probe can never collide:

    [lo + 0x40,    lo + 0x800)  'ship': ff7nx_campreserve
    [lo + 0x800,   lo + SHIP)   'pace': ff7nx_fieldpace's late gates
    [lo + SHIP,    hi)          ff7nx_frameprobe, diagnostic builds only

Each owner checks that ITS part is still the stock bytes before writing, so
a second writer to the same part is refused rather than silently merged.
"""
from __future__ import annotations

import nxmap

DEAD_TRAMPOLINE = 0x620BD3
DEAD_BODY = 0x623D28
SHIP_BYTES = 0x1000
ENTRY_GUARD = 0x40
PACE_AT = 0x800


def region(src, stock=None):
    """(lo, hi) of the dead ARM body in module `src`, proven dead there."""
    import ff7nx_fieldbg
    m = nxmap.Main(src)
    spans = []
    for va in (DEAD_TRAMPOLINE, DEAD_BODY):
        why = ff7nx_fieldbg._liveness(m, va, spans)
        if why:
            raise ValueError('x86 0x%X is not dead: %s' % (va, why))
        spans.append(m.extent(va))
    return m.extent(DEAD_BODY)


def part(src, which, stock=None):
    """(lo, hi) of one owner's part, refused if it was already written."""
    lo, hi = region(src)
    if which == 'ship':
        # never at the entry itself: the liveness proof above asks whether
        # any branch targets the dead function's ENTRY, and a cave placed
        # there would make the next build's proof fail on our own hook.
        p = (lo + ENTRY_GUARD, lo + PACE_AT)
    elif which == 'pace':
        p = (lo + PACE_AT, lo + SHIP_BYTES)
    elif which == 'probe':
        p = (lo + SHIP_BYTES, hi)
    else:
        raise ValueError(which)
    if stock is not None:
        a = nxmap.Main(src).text[p[0]:p[1]]
        b = nxmap.Main(stock).text[p[0]:p[1]]
        if a != b:
            raise ValueError('dead-space part %r at +0x%X was already '
                             'written by another pass' % (which, p[0]))
    return p


class Bump:
    """Contiguous allocator inside one part, 16-byte aligned caves."""

    def __init__(self, lo, hi):
        self.lo = lo
        self.at, self.hi = (lo + 15) & ~15, hi
        self.placed = {}

    def put(self, build):
        n = len(build(0, lambda i: 4 * i))
        entry = self.at
        words = build(entry, lambda i: entry + 4 * i)
        if len(words) != n or entry + 4 * n > self.hi:
            raise ValueError('cave does not fit in dead space')
        for i, w in enumerate(words):
            self.placed[entry + 4 * i] = w
        self.at = (entry + 4 * n + 15) & ~15
        return entry

    def put_bytes(self, data):
        """Read-only data, 16-byte aligned, returned address."""
        at = self.at
        data = bytes(data) + b'\0' * (-len(data) % 4)
        if at + len(data) > self.hi:
            raise ValueError('data does not fit in dead space')
        for i in range(0, len(data), 4):
            self.placed[at + i] = int.from_bytes(data[i:i + 4], 'little')
        self.at = (at + len(data) + 15) & ~15
        return at
