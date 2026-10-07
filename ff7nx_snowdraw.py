#!/usr/bin/env python3
r"""
ff7nx_snowdraw.py -- a longer draw distance for the Gold Saucer snowboard.

    python3 ff7nx_snowdraw.py <ff7_en | sdout>              (report)
    python3 ff7nx_snowdraw.py <ff7_en | sdout> --apply
    python3 ff7nx_snowdraw.py <ff7_en | sdout> --revert

Cost: ff7_en .data bytes plus ONE in-place word in exefs/main. No cave.
SEVENTH_NX_SNOW_DRAW=0 leaves both stock.

WHAT DECIDES HOW FAR AHEAD THINGS APPEAR
========================================
The Gold Saucer course is 5 routes of blocks (table 0x949F00, 32 bytes a
route: count at +0, per-block lookahead BYTES via the pointer at +0x10,
per-block model word via +0x14). Each frame the gameplay state 0x724266 calls
0x726560(1, look + 1) with look = route.lookahead[current block], which
draws blocks cur-1 .. cur+look, queues their models, and SPAWNS each newly
listed block's placed objects (trees, balloons, flags: spawn table 0x9642F8,
one function per global block, run once via the per-block flag 0xDDAD18).
Course blocks and nearly all objects are drawn with render mode 0, which has
no depth fade -- so whatever enters the window appears at full brightness.
That is the pop-in. Stock lookaheads are 1..11 blocks (median ~6, blocks
~1500 units apart): new geometry appears 2.5k-9k units ahead.

THE HARD LIMITS (measured in the x86; all three are respected here)
===================================================================
  block list       0xDD7CF0, 20 entries, capped by the game       <= 14 used
  render queue     0xDD8708, 128 entries, NOT bounds-checked --
                   entry 128 overwrites its own count and three
                   function pointers                              <= 96 used
  object pool      32 entries; several callers write through a
                   NULL slot                                      <= 20 used
Per block: model entries (1..4, 6 when the word is 0) are exact; objects
per spawn function come from a static count of every allocation path
(loops verified by hand, 32 undeterminable functions assumed 5), 2 queue
entries each, plus a 12-entry reserve for Cloud, the board, HUD and effects.

THE RULE
========
look' = the largest value <= min(look + 4, 12) whose window (blocks
cur-1 .. cur+look') stays inside all three budgets, never crossing the end
of its route (forks keep the stock value), never below stock. Then the far
edge cur+look' is made non-decreasing along the route so no block that is
already on screen can drop out and pop back. Routes 0 and 2 (8 and 9 block
connectors) stay stock. The story (Icicle Inn) course, table 0x939CE0, is
untouched in this build.

THE STORY COURSE (BUILD 617c)
=============================
The Icicle Inn run uses a second set of the same tables, picked by
0x722F82 on [0xDD865C] (1 = story): routes 0x939CE0 (7 routes: a 301-block
trunk forking twice into four 50-block legs, 601 blocks), first-block
indices 0x926298, spawn functions 0x9661B0 (run once per block via the
flag at 0xDDC1A8, objects from the same 32-entry pool shape at 0xDDC478).
Its per-block object counts are not hand-derived like the Gold Saucer's:
`_scratch`'s static counter -- calls to the story allocator (0x73BF70 /
0x73CE98) through every helper, a loop or a helper with runtime behaviour
counted as UNKNOWN -- reproduces the Gold Saucer's hand table exactly on
635 of 636 blocks (the 636th is one under), and on the story course every
UNKNOWN is taken as 6. Same rule, same three budgets, all seven routes.

FAR PLANE
=========
16384 units (x86 0x722F40 `push 0x46800000`, ARM +0xE4D528
`mov w8, #0x46800000`, the only site) -> 32768, so the longer window is
never clipped at the back.

BUILD 617c turns it back ON by default. Build 544 turned it off because
build 542 was the first build in which Cloud's eyes flickered. The real
cause turned out to be the eye decal sitting exactly on his skin, which
BUILD 547 fixed by lifting it 2 units (ff7nx_snowface). Depth precision
near the camera is set by the near plane (64); doubling the far plane
barely moves it. With the plane at 32768, a newly listed block may be up to
FAR_DIST (30000) away, and the lookahead may grow by up to K_FAR (8) blocks.
The block-list cap (12 ahead) then decides, rather than the distance limit:
the Gold Saucer's median lookahead goes from 9 to 12 blocks (a median of
12.3k to 14.6k units). SEVENTH_NX_SNOW_FAR=0 restores the stock plane, along
with BUILD 544's 14500 / +4 limits.
"""
from __future__ import annotations

import hashlib
import os
import struct
import sys

ENV = 'SEVENTH_NX_SNOW_DRAW'
TITLE_ID = '0100A5B00BDC6000'
IMAGE_BASE = 0x400000

ROUTE_TABLE = 0x949F00
ROUTES = 5
ROUTE_BASES = 0x9262A8                 # 5 x int16: first global block index
SPAWN_TABLE = 0x9642F8
BLOCKS = 636
SPAWN_SHA1 = 'f28de0c434749fdb41290ec49d89a331e17efdaf'
# per global block: placed-object allocations its spawn function can make
SPAWNS = bytes(int(c, 16) for c in (
    '000100000000200000100010000001000001000010000000100001001001000000100010000010000010000000100000000100000010100013000001000001000000000001005022222212200012002102020001560022122012220010000000001222005000111000000000000402101121120001000555555555001000555555555550010005555555000001000010000000010000032222120400001001110002001030001111111231111121112000011000110010100001010000101000110000100100100000100001001000002010110002000002222001000000000000000010000100010001000001000000000000001000011111111111111111100211100200000000000010000100200111100100120000001202000000000011111111100000010000000000000300020005000020005000000300020011'))
GROW_ROUTES = (1, 3, 4)
K, L_MAX = 4, 12
MAX_BLOCKS, MAX_QUEUE, MAX_POOL = 14, 96, 20
QUEUE_RESERVE = 12
# block positions: route +0x1C, 3 x int32 per block (x, y, z)
MAX_DIST = 14500.0
FAR_ENV = 'SEVENTH_NX_SNOW_FAR'          # 0 = keep the stock far plane
FAR_DIST = 30000.0                     # 617c: inside the 32768 plane
K_FAR = 8

# BUILD 617c: the Icicle Inn (story) course
STORY_ROUTE_TABLE = 0x939CE0
STORY_ROUTES = 7
STORY_ROUTE_BASES = 0x926298
STORY_SPAWN_TABLE = 0x9661B0
STORY_BLOCKS = 601
STORY_SPAWN_SHA1 = '7f911228cc96fc6b488af430881e2dac897155bc'
STORY_SPAWNS = bytes(int(c, 16) for c in (
    '0000000010000001100010000010000210000000300000003010000010100101001000000111100100000100000000000000000000000000000100000000000000002100001000000010100000001000020600000000011201120112011201120112011201120112011201120112000000000000066666666666666666666666666600000300013000000100000000000000000000000100000000000000212222221122021321222120203020200000000000111112000000010111110121022101010102200000010000000000000666000000666666666000000666600000000000000000000002022220022201122002131112266666666001000000000000000063200003224322222420000000000000000000032322000000000000000000000000000000000000000'))
STORY_GROW_ROUTES = tuple(range(STORY_ROUTES))

FAR_SITE = 0xE4D528
FAR_STOCK = 0x52A8D008                 # mov w8, #0x46800000   16384.0
FAR_NEW = 0x52A8E008                   # mov w8, #0x47000000   32768.0
FAR_ANCHORS = ((0xE4D52C, 0xB9000008), (0xE4D540, 0x52A85008))  # str; near 64


def enabled(env=None):
    v = (os.environ if env is None else env).get(ENV, '').strip().lower()
    return v not in ('0', 'off', 'false', 'no', 'stock')


class _Pe:
    def __init__(self, data):
        pe = struct.unpack_from('<I', data, 0x3C)[0]
        nsec = struct.unpack_from('<H', data, pe + 6)[0]
        optsz = struct.unpack_from('<H', data, pe + 20)[0]
        off = pe + 24 + optsz
        self.secs = []
        for i in range(nsec):
            _vs, va, rsize, raw = struct.unpack_from('<IIII', data,
                                                     off + 40 * i + 8)
            self.secs.append((va + IMAGE_BASE, raw, rsize))

    def off(self, va):
        for base, raw, rsize in self.secs:
            if base <= va < base + rsize:
                return raw + va - base
        raise ValueError('VA %#x not in file' % va)


def _routes(data, story=False):
    pe = _Pe(data)
    rd = lambda va, n: data[pe.off(va):pe.off(va) + n]
    nr = STORY_ROUTES if story else ROUTES
    bases = struct.unpack('<%dh' % nr, rd(STORY_ROUTE_BASES if story
                                           else ROUTE_BASES, 2 * nr))
    table = STORY_ROUTE_TABLE if story else ROUTE_TABLE
    out = []
    for k in range(nr):
        e = rd(table + 32 * k, 32)
        n = struct.unpack_from('<h', e, 0)[0]
        ptrs = struct.unpack_from('<5I', e, 12)
        words = struct.unpack('<%dI' % n, rd(ptrs[2], 4 * n))
        ents = [6 if w == 0 else (1 if w < 0x100 else 2 if w < 0x10000
                                  else 3 if w < 0x1000000 else 4)
                for w in words]
        pos = [struct.unpack('<3i', rd(ptrs[4] + 12 * i, 12))
               for i in range(n)]
        out.append({'n': n, 'look_va': ptrs[1], 'base': bases[k],
                    'look': list(rd(ptrs[1], n)), 'ents': ents,
                    'pos': pos})
    return out, pe, rd


def check(data):
    _r, _pe, rd = _routes(data)
    if hashlib.sha1(rd(SPAWN_TABLE, 4 * BLOCKS)).hexdigest() != SPAWN_SHA1:
        return ['spawn table 0x%X is not the measured one' % SPAWN_TABLE]
    if sum(r['n'] for r in _r) != BLOCKS:
        return ['route table does not describe %d blocks' % BLOCKS]
    return []


def check_story(data):
    _r, _pe, rd = _routes(data, story=True)
    if hashlib.sha1(rd(STORY_SPAWN_TABLE, 4 * STORY_BLOCKS)
                    ).hexdigest() != STORY_SPAWN_SHA1:
        return ['story spawn table 0x%X is not the measured one'
                % STORY_SPAWN_TABLE]
    if sum(r['n'] for r in _r) != STORY_BLOCKS:
        return ['story route table does not describe %d blocks'
                % STORY_BLOCKS]
    return []


def _limits():
    """(K, distance limit) -- 617c: the longer reach rides on the far plane."""
    return (K_FAR, FAR_DIST) if far_enabled() else (K, MAX_DIST)


def _window(i, look, r, sp):
    n = r['n']
    j0, j1 = max(0, i - 1), min(n - 1, i + look)
    return (j1 - j0 + 1,
            sum(r['ents'][j0:j1 + 1]) + 2 * sum(sp[i:j1 + 1]) + QUEUE_RESERVE,
            sum(sp[i:j1 + 1]))


def _dist(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def plan(stock_look, r, spawns=None):
    """New lookahead list for one route (from its STOCK values)."""
    n = r['n']
    sp = list((SPAWNS if spawns is None else spawns)[r['base']:r['base'] + n])
    pos = r['pos']
    grow, limit = _limits()
    new = []
    for i in range(n):
        best = stock_look[i]
        # BUILD 544: stay inside the far plane. The newest block may be no
        # further than the distance limit, or than stock already put it at
        # this position if that is further.
        dmax = max(limit, _dist(pos[i], pos[min(n - 1, i + best)]))
        for L in range(stock_look[i] + 1, min(stock_look[i] + grow, L_MAX) + 1):
            if i + L > n - 1:
                break
            if _dist(pos[i], pos[i + L]) > dmax:
                break
            b, q, p = _window(i, L, r, sp)
            if b <= MAX_BLOCKS and q <= MAX_QUEUE and p <= MAX_POOL:
                best = L
            else:
                break
        new.append(best)
    # far edge non-decreasing: walk backwards, never below stock
    for i in range(n - 2, -1, -1):
        edge = min(i + new[i], (i + 1) + new[i + 1])
        new[i] = max(stock_look[i], edge - i)
    return new


def build(data, revert=False):
    """Return (new ff7_en bytes, stats). Raises ValueError on foreign data."""
    bad = check(data)
    if bad:
        raise ValueError('; '.join(bad))
    out = bytearray(data)
    stats = []
    courses = [(False, GROW_ROUTES, _STOCK, SPAWNS)]
    if story_enabled():
        bad = check_story(data)
        if bad:
            raise ValueError('; '.join(bad))
        courses.append((True, STORY_GROW_ROUTES, _STORY_STOCK, STORY_SPAWNS))
    for story, grow_routes, stocks, spawns in courses:
        routes, pe, _rd = _routes(data, story=story)
        for k in grow_routes:
            stats.append(_grow_route(out, pe, routes[k], k, stocks[k], spawns,
                                     revert, story))
    return bytes(out), stats


def _grow_route(out, pe, r, k, stock, spawns, revert, story):
    if True:
        cur = r['look']
        mine = plan(stock, r, spawns)
        target = stock if revert else mine
        if cur != stock and cur != mine and not all(
                c >= s for c, s in zip(cur, stock)):
            raise ValueError('%s route %d lookahead is neither stock nor ours'
                             % ('story' if story else 'Gold Saucer', k))
        o = pe.off(r['look_va'])
        out[o:o + r['n']] = bytes(target)
        sp = list(spawns[r['base']:r['base'] + r['n']])
        grown = [i for i in range(r['n']) if target[i] > stock[i]] or [0]
        worst = [max(_window(i, target[i], r, sp)[j] for i in grown)
                 for j in range(3)]
        return (('story ' if story else '') + str(k),
                sorted(stock)[len(stock) // 2],
                sorted(target)[len(target) // 2], max(target), worst)


def _stock_tables():
    return {1: list(bytes.fromhex(_S1)), 3: list(bytes.fromhex(_S3)),
            4: list(bytes.fromhex(_S4))}


_S1 = ('050403040a0908070807060606060606080808080706060606060605040306060b0a0a09080706050506060b0a0a090807060505050606050606050605040406060707060606060606060606060707060605050506060b0a0a09080706050507060504030408070605040606060505060706050508080706060808060606060505040404040404040404040404040404040404040404040404040404040404040404040404040404040404040404040404040404040404040404040404040706050403020202')
_S3 = ('05030202080808090a090908070605050505050707070707070707060505040505050606060606060606070706050504050505070707070908070706060505090807060505040807060505050a09080708080808080706050504040504030304050807070707070808080808080707070605060606060606060606060606060807080808070607060504050404030405040307060504030708070605040304060706050403040605040305070a0a0909080807060807070606060605060504030808080809080706050403020101')
_S4 = ('0503020207070605050604030202060606060606060a09080707060504040606050404060504040505040405050406050506050407080708090807070605040505040606050404040706050404040407060504040404040507060505040505060505070605070605050504040506060504060606060606060606060606060606060606050403060606050403070707060504030306050807070707070908070605040605050706050407060506080808080706060505050706050407060505060605050407060505040404070605080706050403020101')
_STOCK = _stock_tables()
# BUILD 617c: the story course's stock lookaheads, routes 0..6
_STORY_STOCK = {k: list(bytes.fromhex(h)) for k, h in {
    0: '03040506060606060808090909090908070707070707070707070708080807060607060606060606060606060606060606060706060606050508070605050508070606050508070605050508070605050508070605050505060707060605050505050505060605050505050505050506060505050505050505050507060606060606070808080808080808080808080808080808080808080808080807070706060606060707070606060606060606060606060606060606060606060606060606060606060606060606060606060606060606060606060707070708090908080807070707060504040404040404040404040404040404040404040404040404040506060606060606060606060707060609090908080706060606060606060606060606060606060707060605',
    1: '0708090909080707070707070606060505050505050505050505050505050505050505050505050605040303030707070707',
    2: '0506060606060606060606060505050505050505050505050505050505050505050505050505050606060606060707070606',
    3: '0707070606060606060707070707070707070706060606060606060606060606060606060606060606060606060606060606',
    4: '0606060909090807070605050706060505050505060606060606070707060505050505060707060504040606060606060606',
    5: '0608080605050507060505050404050706050505040508070605050504040404050505050605060505050506050505050505',
    6: '0605040303020204050504030305050605050505060606060706060505070707070708080706060505060606060606060606',
}.items()}
STORY_ENV = 'SEVENTH_NX_SNOW_DRAW_STORY'


def story_enabled(env=None):
    v = (os.environ if env is None else env).get(STORY_ENV, '').strip().lower()
    return v not in ('0', 'off', 'false', 'no', 'stock')


def _exe_path(target):
    if os.path.isdir(target):
        return os.path.join(target, 'atmosphere', 'contents', TITLE_ID,
                            'romfs', 'ff7', 'resources', 'ff7_1.02', 'ff7_en')
    return target


def _main_path(target):
    if os.path.isdir(target):
        return os.path.join(target, 'atmosphere', 'contents', TITLE_ID,
                            'exefs', 'main')
    return target


def apply_exe(target, revert=False, log=print):
    path = _exe_path(str(target))
    try:
        data = open(path, 'rb').read()
        new, stats = build(data, revert)
    except (OSError, ValueError) as exc:
        log('  ! snowboard draw distance (ff7_en): %s -- NOT CHANGED' % exc)
        return 1
    if new != data:
        with open(path + '.snowdraw-tmp', 'wb') as fh:
            fh.write(new)
        os.replace(path + '.snowdraw-tmp', path)
    for k, med0, med1, mx, (b, q, pl) in stats:
        log('  snowboard route %s lookahead median %d -> %d blocks (max %d); '
            'worst lengthened window %d blocks / %d queue / %d pool (budget %d / %d / %d)'
            % (k, med0, med1, mx, b, q, pl, MAX_BLOCKS, MAX_QUEUE, MAX_POOL))
    return 0


def far_enabled(env=None):
    """617c: ON unless SEVENTH_NX_SNOW_FAR=0 (see FAR PLANE above)."""
    v = (os.environ if env is None else env).get(FAR_ENV, '').strip().lower()
    return v not in ('0', 'off', 'false', 'no', 'stock')


def apply_nso(target, revert=False, log=print):
    """BUILD 544: OFF by default. Build 542 raised the far plane to 32768;
    that build is the first in which Cloud's eyes flickered at the end of
    the race -- a decal on his face fighting the head for depth once the
    depth range doubled. The lookahead now stays inside the stock 16384
    instead (MAX_DIST), so the stock plane is kept and this only runs with
    SEVENTH_NX_SNOW_FAR=1. When off it RESTORES the stock word."""
    if not revert and not far_enabled():
        revert = True
    import nso_patcher
    path = _main_path(str(target))
    try:
        nso = nso_patcher.read_nso(nso_patcher.Path(path))
        t = next(bytes(sg.data) for sg in nso.segments if sg.name == '.text')
        w = lambda va: struct.unpack_from('<I', t, va)[0]
        for va, want in FAR_ANCHORS:
            if w(va) != want:
                raise ValueError('far plane anchor +%#x holds %08X' % (va, w(va)))
        cur = w(FAR_SITE)
        want = FAR_STOCK if revert else FAR_NEW
        if cur not in (FAR_STOCK, FAR_NEW):
            raise ValueError('far plane site holds %08X' % cur)
        if cur == want:
            return 0
        spec = {'name': 'snowboard far plane', 'patches': [{
            'name': 'snowboard far plane', 'va': '0x%X' % FAR_SITE,
            'expect': struct.pack('<I', cur).hex(' '),
            'set': struct.pack('<I', want).hex(' ')}]}
        for line in nso_patcher.apply_spec(nso, spec):
            log('    ' + line)
        nso_patcher.Path(path).write_bytes(nso_patcher.rebuild(nso))
    except (OSError, ValueError, StopIteration, nso_patcher.PatchError) as exc:
        log('  ! snowboard far plane: %s -- NOT CHANGED' % exc)
        return 1
    log('  snowboard far plane %s' % ('16384 (stock)' if revert
                                       else '16384 -> 32768'))
    return 0


def main(argv):
    if not argv:
        print(__doc__.split('\n\n')[1])
        return 2
    t = argv[0]
    if '--apply' in argv or '--revert' in argv:
        rv = '--revert' in argv
        rc = apply_exe(t, rv)
        if os.path.isdir(t):
            rc |= apply_nso(t, rv)
        return rc
    data = open(_exe_path(t), 'rb').read()
    print(check(data) or 'anchors ok')
    new, stats = build(data)
    for st in stats:
        print(st)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
