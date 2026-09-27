#!/usr/bin/env python3
r"""
ff7nx_coastercull.py -- the roller coaster's visibility lists, widened for 16:9.

    python3 ff7nx_coastercull.py <coaster.lgp>            (report)

DATA ONLY: one entry (xbin.bin) of coaster.lgp. No code, nothing in exefs/main.
SEVENTH_NX_COASTER_CULL=0 leaves it stock; SEVENTH_NX_COASTER_CULL_STEPS=
"lead,lag" (default 3,5) sets how far the lists are widened.

HOW THE COASTER DECIDES WHAT IS DRAWN
=====================================
The coaster does no per-frame frustum test. Its visibility was baked on the
PSX, for a 4:3 view, into four streams inside xbin.bin (the file table in
xbinadr.bin, entries 2, 3, 8 and 9 -- read by x86 0x5ED8F0 and nothing else):

    entry 2  track polygons that ENTER the view    -> 0x5EDFE7 (list 0xC476F0)
    entry 3  track polygons that LEAVE the view    -> 0x5EE09A
    entry 8  scenery polygons that enter           -> 0x5EDE71 (list 0xC503B0)
    entry 9  scenery polygons that leave           -> 0x5EDF18

Each stream is 2141 groups of u16 polygon ids, one group per ride step
(progress >> 18, x86 0x5EDA91 / 0x5EDC59), each group ended by 0xFFFF. As the
car moves the game applies each step's group: add these to the draw list,
remove those. Whatever is in the list is drawn (0x5E9F33 / 0x5E9E7E), and a
polygon wholly behind the near plane is skipped there, so an extra entry costs
draw time and nothing else. The draw buffers grow on demand (0x66E272), so
there is no fixed capacity to overrun.

In 16:9 the view is a third wider, so a polygon leaves the 4:3 edge -- and is
removed -- while it is still on screen. That is the texture vanishing at the
sides. The fix is to widen every visible interval: enter LEAD steps earlier,
leave LAG steps later. A step is 1/25..1/10 s of ride at the game's speed
range (0xA7F8..0x1D4C0 progress a tick, 2^18 a step).

WHY IT IS DONE ON INTERVALS, NOT BY SHIFTING THE STREAMS
========================================================
The list code has no presence check: adding a polygon that is already listed
corrupts the list. Shifting the "add" stream earlier and the "remove" stream
later would do exactly that whenever a polygon leaves and comes back within
LEAD + LAG steps (many do: 1-4 step gaps are common). So every stream is
replayed into per-polygon intervals, each interval widened, overlapping ones
MERGED, and the streams rebuilt from the result. Every add then has a remove
after it and before its next add -- checked by replaying the new streams.

Merging can only lower the entry count, so each rebuilt stream fits in the
bytes its original occupied; the rest is filled with 0xFFFF (empty groups),
after the original stream's tail word, which is kept.
"""
from __future__ import annotations

import os
import struct
import sys

ENV = 'SEVENTH_NX_COASTER_CULL'
STEPS_ENV = 'SEVENTH_NX_COASTER_CULL_STEPS'
DEFAULT_STEPS = (3, 5)
XBIN = 'xbin.bin'
XBINADR = 'xbinadr.bin'
STREAMS = ((2, 3, 'track'), (8, 9, 'scenery'))
GROUPS = 2141
END = 0xFFFF


def enabled(env=None) -> bool:
    v = (os.environ if env is None else env).get(ENV, '').strip().lower()
    return v not in ('0', 'off', 'false', 'no', 'stock')


def steps(env=None):
    raw = (os.environ if env is None else env).get(STEPS_ENV, '').strip()
    try:
        lead, lag = (int(x) for x in raw.split(','))
        if 0 <= lead <= 16 and 0 <= lag <= 16:
            return lead, lag
    except ValueError:
        pass
    return DEFAULT_STEPS


def _offsets(adr):
    t = struct.unpack('<%dI' % (len(adr) // 4), adr)
    return [x - t[0] for x in t]


def _parse(xb, lo, hi):
    words = struct.unpack('<%dH' % ((hi - lo) // 2), xb[lo:hi])
    groups, cur, n = [], [], 0
    for n, v in enumerate(words):
        if v == END:
            groups.append(cur)
            cur = []
            if len(groups) == GROUPS:
                break
        else:
            cur.append(v)
    if len(groups) != GROUPS:
        raise ValueError('stream at 0x%X has %d groups, expected %d'
                         % (lo, len(groups), GROUPS))
    return groups, list(words[n + 1:])


def _replay(adds, rems):
    """Per-polygon [start, end) intervals; end None = still listed at the end.
    Raises on anything the list code could not survive."""
    listed, out = {}, []
    for s in range(len(adds)):
        for k, e in enumerate(adds[s]):
            if e in listed:
                raise ValueError('polygon %d added twice (step %d)' % (e, s))
            listed[e] = (s, k)
        for k, e in enumerate(rems[s]):
            if e not in listed:
                raise ValueError('polygon %d removed while not listed '
                                 '(step %d)' % (e, s))
            out.append((e, listed.pop(e), (s, k)))
    for e, sk in listed.items():
        out.append((e, sk, None))
    return out


def widen(adds, rems, lead, lag):
    n = len(adds)
    per = {}
    for e, akey, rkey in _replay(adds, rems):
        a2 = max(0, akey[0] - lead)
        r2 = None if rkey is None or rkey[0] + lag >= n else rkey[0] + lag
        per.setdefault(e, []).append([a2, r2, akey, rkey])
    nadd = [[] for _ in range(n)]
    nrem = [[] for _ in range(n)]
    for e, ivs in per.items():
        ivs.sort(key=lambda v: v[0])
        merged = []
        for a, r, akey, rkey in ivs:
            if merged and (merged[-1][1] is None or a <= merged[-1][1]):
                last = merged[-1]
                if last[1] is None or r is None:
                    last[1] = None
                elif r >= last[1]:
                    last[1], last[3] = r, rkey
            else:
                merged.append([a, r, akey, rkey])
        for a, r, akey, rkey in merged:
            nadd[a].append((akey, e))
            if r is not None:
                nrem[r].append((rkey, e))
    # keep the stock order inside a group (the draw list is append-ordered)
    nadd = [[e for _k, e in sorted(g)] for g in nadd]
    nrem = [[e for _k, e in sorted(g)] for g in nrem]
    return nadd, nrem


def _emit(groups, tail, size):
    words = []
    for g in groups:
        words.extend(g)
        words.append(END)
    words.extend(tail[:1])
    if 2 * len(words) > size:
        raise ValueError('rebuilt stream is %d bytes, the slot is %d'
                         % (2 * len(words), size))
    words.extend([END] * (size // 2 - len(words)))
    return struct.pack('<%dH' % len(words), *words)


def transform(xb, adr, lead, lag):
    """New xbin.bin bytes and a report dict."""
    off = _offsets(adr)
    out = bytearray(xb)
    report = {}
    for ai, ri, what in STREAMS:
        adds, atail = _parse(xb, off[ai], off[ai + 1])
        rems, rtail = _parse(xb, off[ri], off[ri + 1])
        nadd, nrem = widen(adds, rems, lead, lag)
        _replay(nadd, nrem)                        # the new lists are sound
        for idx, groups, tail in ((ai, nadd, atail), (ri, nrem, rtail)):
            lo, hi = off[idx], off[idx + 1]
            out[lo:hi] = _emit(groups, tail, hi - lo)
        peak = cur = 0
        peak0 = cur0 = 0
        for s in range(GROUPS):
            cur += len(nadd[s]) - len(nrem[s])
            cur0 += len(adds[s]) - len(rems[s])
            peak, peak0 = max(peak, cur), max(peak0, cur0)
        report[what] = (sum(map(len, adds)), sum(map(len, nadd)), peak0, peak)
    return bytes(out), report


def patch_archive_entries(xb, adr, env=None):
    lead, lag = steps(env)
    new, rep = transform(xb, adr, lead, lag)
    lines = ['visibility widened by %d step(s) ahead, %d behind' % (lead, lag)]
    for what, (n0, n1, p0, p1) in rep.items():
        lines.append('%s: %d -> %d list entries, most listed at once %d -> %d'
                     % (what, n0, n1, p0, p1))
    return new, '; '.join(lines)


def main():
    import lgp
    a = lgp.Archive(sys.argv[1])
    xb = a.index[XBIN]['payload']
    adr = a.index[XBINADR]['payload']
    new, note = patch_archive_entries(xb, adr)
    print(note)
    print('changed' if new != xb else 'unchanged')


if __name__ == '__main__':
    main()
