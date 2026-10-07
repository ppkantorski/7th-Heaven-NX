#!/usr/bin/env python3
"""ff7nx_palauto.py -- ff7nx_palstates for every field whose palette effect
follows the common pattern, found and proven automatically. BUILD 618x.

The junbin5 lamp (618w, hand-made) is the common case of a palette effect:
ONE byte variable drives ONE brightness operation (ADPAL add, MPPAL2
multiply, or MPPAL on the whole palette), immediately followed by the LDPAL
that writes the effect's palette. This module finds every such effect in a
field, works out the states it takes, proves that reading against Cosmos's
own frames, and hands the result to ff7nx_palstates.plan_field.

PER EFFECT (palette q), all must hold or the field is left untouched:
  * q is written by exactly ONE LDPAL in the whole script, and that LDPAL
    directly follows a value op (ADPAL E9 / MPPAL2 EA / MPPAL DF) whose
    source and destination buffers are literal and different, whose colour
    operands are literals or one bank-5 byte variable v, and whose source
    buffer is filled by an STPAL (literal palette) or a CPPAL of one.
    Nothing else reads the destination buffer.
  * every instruction in the script that touches v is one this module can
    rewrite: SETBYTE v,literal; PLUS/MINUS v,literal; INC/DEC v; IFUB/IFUBL
    v against a literal; the value op itself. No word (bank 6) access
    overlaps v.
  * STATES: v's values lie on a lattice (start = a literal it is set to,
    step = the gcd of its PLUS/MINUS steps). The palette the script would
    load for each value is rendered at 1x through the field's own data and
    matched against Cosmos's frames (the HD pictures FFNx swaps per palette
    state). The states are the lattice values between the extremes of the
    literals and the matched frames. Each state must match a frame, every
    frame must be some state's, and there may be at most 8 states (one
    background parameter). This is the proof that the reading of the script
    is right: a wrong reading does not reproduce Cosmos's frames one-to-one.
  * every record of q is an additive FX record with param 0 on a paletted
    FX page, and has a unique vanilla twin (Cosmos's sheet layout).
REWRITE: v is renumbered to the state index (every literal mapped onto the
lattice; comparisons keep their outcome on every reachable value), and the
value op + LDPAL become BGCLR P ... BGON P,v of the same length. P is a
background parameter the field does not use.

Fields with a hand-made spec (ff7nx_palstates.SPECS) are skipped here.
SEVENTH_NX_NO_PAL_AUTO=1 disables; SEVENTH_NX_PAL_AUTO_ONLY=a,b limits;
SEVENTH_NX_PAL_AUTO_SKIP=a,b excludes fields.
"""
from __future__ import annotations

import collections
import math
import os
import struct

import numpy as np

OFF_ENV = 'SEVENTH_NX_NO_PAL_AUTO'
ONLY_ENV = 'SEVENTH_NX_PAL_AUTO_ONLY'
SKIP_ENV = 'SEVENTH_NX_PAL_AUTO_SKIP'
MAX_STATES = 8            # one background parameter, 8 state bits
MAX_MULTI = 62            # one parameter per state (64 exist; 0 is static)
MIN_MARGIN = 0.1          # ... and beat its rolled-cells control by this
DIM = 0.25                # states under this x the brightest skip the check
ALIGN_TOL = 0.03          # alignment residual, fraction of the brightness span
ALIGN_MIN = 0.85          # frames may miss a few states (aligned by brightness)
MIN_CORR = 0.15           # Cosmos frame vs the state's 1x render, per page
MUL_SHIFT = 6             # MPPAL / MPPAL2 multiply: c * m >> 6 (eals_1 fit)
EXCLUDE = frozenset()     # fields reviewed and refused by hand


def _env_set(name):
    raw = os.environ.get(name, '').strip()
    return frozenset(x.strip().lower() for x in raw.split(',') if x.strip())


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


# ------------------------------------------------------------------ decode
def _stream(sec0):
    import echo_s_flevel as ES
    out = []
    for a, b in ES._routine_blocks(sec0):
        st, cut = ES._decode_block(sec0, a, b)
        if cut:
            raise ValueError('routine at %d does not decode' % a)
        out.extend((a, o, op, z) for o, op, z in st)
    return out


def _value_op(s, o, op):
    """{'S','D','size','start','ch': [(bank, byte) x3 as B,G,R]}."""
    b12, b34, b5 = s[o + 1], s[o + 2], s[o + 3] & 15
    if op in (0xE9, 0xEA):
        if b12:
            raise ValueError('buffer from a variable')
        return {'S': s[o + 4], 'D': s[o + 5], 'start': 0,
                'ch': [(b34 >> 4, s[o + 6]), (b34 & 15, s[o + 7]),
                       (b5, s[o + 8])], 'size': s[o + 9] + 1}
    if op == 0xDF:
        if (b12 >> 4) or b5:
            raise ValueError('MPPAL start/size from a variable')
        return {'S': s[o + 4], 'D': s[o + 5], 'start': s[o + 6],
                'ch': [(b12 & 15, s[o + 7]), (b34 >> 4, s[o + 8]),
                       (b34 & 15, s[o + 9])], 'size': s[o + 10] + 1}
    raise ValueError('op %02X' % op)


def _apply(op, cols5, vo, val):
    """Colours (n, 3: r, g, b in 0..31) after the value op with v = val."""
    out = cols5.copy()
    lo, hi = vo['start'], min(len(cols5), vo['start'] + vo['size'])
    # channel order in the op is B, G, R
    vals = [val if bank else lit for bank, lit in vo['ch']]
    b, g, r = vals
    seg = cols5[lo:hi].astype(np.int64)
    if op == 0xE9:
        sv = [x - 256 if x >= 128 else x for x in (r, g, b)]
        seg = seg + np.array(sv)[None]
    else:
        seg = (seg * np.array([r, g, b])[None]) >> MUL_SHIFT
    out[lo:hi] = np.clip(seg, 0, 31)
    return out


# --------------------------------------------------------------- analysis
def find_effects(sec0):
    """[{q, src_pal, op, vo, var, sites: [(offset, length, cppal size)]}]
    for every palette whose every writer is `value op [CPPAL S->D] LDPAL`
    with one identical value op driven by one bank-5 variable. Raises for
    the field when a palette is written in a way this cannot follow."""
    s = sec0
    st = _stream(s)
    writers = collections.defaultdict(list)
    for i, (_a, o, op, z) in enumerate(st):
        if op == 0xE6:
            if s[o + 1]:
                raise ValueError('LDPAL with a variable operand')
            writers[s[o + 3]].append(i)
        elif op in (0xEC, 0xE8, 0xEE, 0xED, 0xEF):
            raise ValueError('palette op %02X not handled' % op)
    effects = []
    for q, idx in writers.items():
        sites, sig, ok = [], None, True
        for i in idx:
            a, o, op, z = st[i]
            buf = s[o + 2]
            j = i - 1
            cp = None
            if j >= 0 and st[j][0] == a and st[j][2] == 0xE7:
                _a, oc, _op, zc = st[j]
                if s[oc + 1] or s[oc + 3] != buf:
                    ok = False
                    break
                cp = (s[oc + 2], s[oc + 4] + 1)
                j -= 1
            if j < 0 or st[j][0] != a or st[j][2] not in (0xE9, 0xEA, 0xDF):
                ok = False
                break
            _a2, vo_off, vop, vz = st[j]
            try:
                vo = _value_op(s, vo_off, vop)
            except ValueError:
                ok = False
                break
            if vo['D'] != buf or vo['S'] == vo['D']:
                ok = False
                break
            if cp is not None and cp[0] != vo['S']:
                ok = False
                break
            key = (vop, s[vo_off + 1:vo_off + vz], cp)
            if sig is None:
                sig = key
            elif key != sig:
                ok = False
                break
            sites.append((vo_off, (o + z) - vo_off, a, j))
        if not ok or not sites:
            continue
        vop, _b, cp = sig
        vo = _value_op(s, sites[0][0], vop)
        banks = {bk for bk, _l in vo['ch'] if bk}
        addrs = {lit for bk, lit in vo['ch'] if bk}
        if banks != {5} or len(addrs) != 1:
            continue
        # the source buffer: an STPAL p -> S (or a CPPAL from one) before
        # the first site, in its routine
        a0, j0 = sites[0][2], sites[0][3]
        src_pal = None
        for _a3, o3, op3, _z3 in st[:j0]:
            if _a3 != a0:
                continue
            if op3 == 0xE5 and not s[o3 + 1] and s[o3 + 3] == vo['S']:
                src_pal = s[o3 + 2]
            if op3 == 0xE7 and not s[o3 + 1] and s[o3 + 3] == vo['S']:
                src_pal = ('cp', s[o3 + 2])
        if isinstance(src_pal, tuple):
            sb = src_pal[1]
            src_pal = None
            for _a3, o3, op3, _z3 in st[:j0]:
                if _a3 == a0 and op3 == 0xE5 and not s[o3 + 1] and \
                        s[o3 + 3] == sb:
                    src_pal = s[o3 + 2]
        if src_pal is None:
            continue
        # S is only ever filled by STPAL (no copy or op writes into it)
        if any((op3 == 0xE7 and s[o3 + 3] == vo['S'])
               or (op3 in (0xE9, 0xEA, 0xDF) and s[o3 + 5] == vo['S'])
               for _a3, o3, op3, _z3 in st):
            continue
        if vo['start'] > 1 or vo['start'] + vo['size'] < 256:
            # partial op (entry 0 is the key and may be skipped): the rest
            # of D must be a copy of S made in the same routine
            if not any(_a3 == a0 and op3 == 0xE7 and not s[o3 + 1]
                       and s[o3 + 2] == vo['S'] and s[o3 + 3] == vo['D']
                       and s[o3 + 4] == 0xFF
                       for _a3, o3, op3, _z3 in st[:j0]):
                continue
        # D is read only by this palette's LDPALs
        readers = sum(1 for _a3, o3, op3, _z3 in st
                      if (op3 in (0xE6, 0xE7) and s[o3 + 2] == vo['D'])
                      or (op3 in (0xE9, 0xEA, 0xDF) and s[o3 + 4] == vo['D']))
        if readers != len(sites):
            continue
        effects.append({'q': q, 'src_pal': src_pal, 'op': vop, 'vo': vo,
                        'cp': cp, 'var': addrs.pop(),
                        'sites': [(o_, l_) for o_, l_, _a, _j in sites]})
    return effects


def _refs(sec0, o, op, z):
    """[(operand offset, bank, addresses covered)] for the variable operands
    of one instruction; None when its layout is unknown."""
    import ff7nx_opbanks as OB
    t = OB.OPS.get(op)
    if t is None or t[0] != z:
        return None
    out = []
    for k, w, bo, which in t[1]:
        bb = sec0[o + bo]
        bank = bb >> 4 if which == 'hi' else (bb & 15 if which == 'lo'
                                              else bb)
        if not bank:
            continue
        a = sec0[o + k]
        cover = (a, a + 1) if bank in (2, 4, 6, 12, 14, 7) else (a,)
        out.append((k, bank, cover))
    return out


def var_uses(sec0, v, skip=()):
    """[(offset, kind, operand offset)] of every instruction touching
    var5[v] (operand layouts from kujata, ff7nx_opbanks); raises on any use
    this cannot rewrite, and on any instruction whose layout is unknown and
    which carries the byte."""
    s = sec0
    out = []
    for _a, o, op, z in _stream(s):
        if o in skip:
            continue
        refs = _refs(s, o, op, z)
        if refs is None:
            if v in s[o + 1:o + z]:
                raise ValueError('op %02X (layout unknown) near var5[%d]'
                                 % (op, v))
            continue
        hit = [(k, bank) for k, bank, cover in refs
               if bank in (5, 6) and v in cover]
        if not hit:
            continue
        if any(bank == 6 for _k, bank in hit):
            raise ValueError('word access overlaps var5[%d]' % v)
        if len(hit) > 1 or len(refs) > 1:
            raise ValueError('op %02X: var5[%d] with another variable'
                             % (op, v))
        k = hit[0][0]
        if op in (0x80, 0x85, 0x87, 0x76, 0x78) and k == 2:
            out.append((o, {0x80: 'set', 0x85: 'plus', 0x76: 'plus',
                            0x87: 'minus', 0x78: 'minus'}[op], 3))
        elif op in (0x95, 0x97) and k == 2:
            out.append((o, 'inc' if op == 0x95 else 'dec', None))
        elif op in (0x14, 0x15) and k in (2, 3):
            out.append((o, 'ifl' if k == 2 else 'ifr', 3 if k == 2 else 2))
        else:
            raise ValueError('op %02X uses var5[%d]' % (op, v))
    return out


def lattice(sec0, uses):
    """(step, anchor, lo, hi): the values the variable can hold when the
    palette is loaded, from its own instructions.

    A comparison bounds the range according to what it guards (IFUB runs
    the next instruction only when true):
      v < c (or <=) guarding PLUS d   -> the top is the last value passing + d
      v > c (or >=) guarding MINUS d  -> the bottom is the first passing - d
      v > c (or >=) guarding SETBYTE  -> a reset: values that pass are reset
                                         before they are shown (top = last
                                         value failing), and mirrored for <
      anything else (==, !=, guarding another variable) -> c itself."""
    s = sec0
    steps, sets = [], []
    for o, kind, k in uses:
        if kind in ('plus', 'minus'):
            steps.append(s[o + 3])
        elif kind in ('inc', 'dec'):
            steps.append(1)
        elif kind == 'set':
            sets.append(s[o + 3])
    steps = [x for x in steps if x]
    if not steps:
        raise ValueError('variable never steps')
    if not sets:
        raise ValueError('variable never set to a literal')
    step = 0
    for x in steps:
        step = math.gcd(step, x)
    anchor = sets[0]
    if any((x - anchor) % step for x in sets):
        raise ValueError('set literals off one lattice')
    by_off = {o: (kind, k) for o, kind, k in uses}
    lat = lambda x: (x - anchor) % step == 0
    lows, highs = list(sets), list(sets)
    for o, kind, k in uses:
        if kind not in ('ifl', 'ifr'):
            continue
        c, cmp = s[o + k], s[o + 4]
        if kind == 'ifr':
            cmp = {2: 3, 3: 2, 4: 5, 5: 4}.get(cmp, cmp)
        z = 6 if s[o] == 0x14 else 7
        g = by_off.get(o + z)
        passing = {2: lambda x: x > c, 3: lambda x: x < c,
                   4: lambda x: x >= c, 5: lambda x: x <= c}.get(cmp)
        grid = [x for x in range(256) if lat(x)]
        if passing and g and g[0] == 'plus' and cmp in (3, 5):
            ok = [x for x in grid if passing(x)]
            highs.append(max(ok) + s[o + z + 3] if ok else c)
        elif passing and g and g[0] == 'minus' and cmp in (2, 4):
            ok = [x for x in grid if passing(x)]
            lows.append(min(ok) - s[o + z + 3] if ok else c)
        elif passing and g and g[0] == 'set':
            bad = [x for x in grid if not passing(x)]
            if cmp in (2, 4) and bad:
                highs.append(max(x for x in bad if x <= 255))
            elif cmp in (3, 5) and bad:
                lows.append(min(bad))
            else:
                raise ValueError('reset guard %d' % cmp)
        else:
            lows.append(c)
            highs.append(c)
    lo = min(x for x in lows)
    hi = max(x for x in highs)
    lo = anchor + math.ceil((lo - anchor) / step) * step
    hi = anchor + math.floor((hi - anchor) / step) * step
    if lo < 0 or hi > 255:
        raise ValueError('range %d..%d leaves a byte' % (lo, hi))
    return step, anchor, lo, hi, sets


# ------------------------------------------------------------------ states
def _cmp_map(c, cmp, lo, step, side):
    """Literal for the renumbered variable keeping `v <cmp> c` (side 'l') or
    `c <cmp> v` (side 'r') true on exactly the same lattice values."""
    x = (c - lo) / step
    if side == 'r':
        cmp = {2: 3, 3: 2, 4: 5, 5: 4}.get(cmp, cmp)
    if cmp in (0, 1):
        if x != int(x):
            return None
        return int(x)
    if cmp in (2, 5):          # v > c  /  v <= c
        return math.floor(x)
    if cmp in (3, 4):          # v < c  /  v >= c
        return math.ceil(x)
    return None


def renumber(sec0, uses, lo, step, n, base=0):
    """Section 0 with var5[v] renumbered to base + (v - lo) / step."""
    out = bytearray(sec0)
    for o, kind, k in uses:
        if kind == 'set':
            c = sec0[o + 3]
            if (c - lo) % step or c < lo or (c - lo) // step >= n:
                raise ValueError('var set to %d off the states' % c)
            out[o + 3] = base + (c - lo) // step
        elif kind in ('plus', 'minus'):
            if sec0[o + 3] % step:
                raise ValueError('step %d' % sec0[o + 3])
            out[o + 3] = sec0[o + 3] // step
        elif kind in ('inc', 'dec'):
            if step != 1:
                raise ValueError('INC/DEC with lattice step %d' % step)
        elif kind in ('ifl', 'ifr'):
            cmp = sec0[o + 4]
            c2 = _cmp_map(sec0[o + k], cmp, lo, step,
                          'l' if kind == 'ifl' else 'r')
            if c2 is None or not 0 <= base + c2 <= 255:
                raise ValueError('comparison %d with %d cannot be kept'
                                 % (cmp, sec0[o + k]))
            out[o + k] = base + c2
    return bytes(out)


def bg_fill(length, param, var):
    """BGCLR param (x k) then BGON param,var5[var] (x m), 3k+4m = length."""
    for m in range(1, length // 4 + 1):
        r = length - 4 * m
        if r >= 3 and r % 3 == 0:
            return (bytes((0xE4, 0x00, param)) * (r // 3)
                    + bytes((0xE0, 0x05, param, var)) * m)
    raise ValueError('cannot fill %d bytes' % length)


def _align(s_ord, els, f_ord, cls, iters=12):
    """{state: frame} for fewer frames than states: a monotone assignment
    minimising |Cosmos brightness - (a * 1x brightness + b)|, a and b
    refitted each round. Raises when even the best fit is poor."""
    M, N = len(s_ord), len(f_ord)
    el = np.array(els, np.float64)
    cl = np.array(cls, np.float64)
    pos = np.round(np.linspace(0, M - 1, N)).astype(int)
    for _ in range(iters):
        A = np.stack([el[pos], np.ones(N)], 1)
        (ga, gb), *_r = np.linalg.lstsq(A, cl, rcond=None)
        pred = ga * el + gb
        cost = (cl[:, None] - pred[None]) ** 2         # (N, M)
        D = np.full((N, M), np.inf)
        back = np.zeros((N, M), int)
        D[0] = cost[0]
        for i in range(1, N):
            best = np.minimum.accumulate(D[i - 1])
            arg = np.zeros(M, int)
            cur = 0
            for m in range(M):
                if D[i - 1][m] <= D[i - 1][cur]:
                    cur = m
                arg[m] = cur
            D[i, 1:] = best[:-1] + cost[i, 1:]
            back[i, 1:] = arg[:-1]
        m = int(np.argmin(D[N - 1]))
        new = [m]
        for i in range(N - 1, 0, -1):
            m = back[i, m]
            new.append(m)
        new = np.array(new[::-1])
        if np.array_equal(new, pos):
            break
        pos = new
    resid = float(np.sqrt(((cl - (ga * el[pos] + gb)) ** 2).mean()))
    span = float(cl.max() - cl.min()) or 1.0
    if ga <= 0 or resid > ALIGN_TOL * span:
        raise ValueError('frames do not align with the states (%.3f)'
                         % (resid / span))
    return {s_ord[p]: f_ord[i] for i, p in enumerate(pos)}


def _free_block(free, n):
    """n consecutive free parameters, highest first (out of the way)."""
    fs = set(free)
    for top in range(63, n - 1, -1):
        blk = list(range(top - n + 1, top + 1))
        if all(p in fs for p in blk):
            return blk
    return None


def _free_var(sec0, taken=()):
    """A bank-5 byte no instruction touches (nor a bank-6 word over it).
    Field temporaries start at 0, and 0 is the static parameter, so its
    first use (BGOFF 0) is harmless."""
    near = set()
    for _a, o, op, z in _stream(sec0):
        refs = _refs(sec0, o, op, z)
        if refs is None:
            near.update(sec0[o + 1:o + z])
            continue
        for _k, bank, cover in refs:
            if bank in (5, 6):
                near.update(cover)
    for a in range(0xFF, 0x80, -1):
        if a not in taken and a not in near:
            return a
    raise ValueError('no free temporary variable')


def bg_multi(length, var, prev, filler):
    """BGOFF param var5[prev]; BGON param var5[var]; var5[prev] = var5[var];
    padded with BGON var5[var] (4) / BGCLR filler (3) to `length`."""
    core = (bytes((0xE1, 0x50, prev, 0x00)) + bytes((0xE0, 0x50, var, 0x00))
            + bytes((0x80, 0x55, prev, var)))
    rest = length - len(core)
    for m in range(rest // 4, -1, -1):
        r = rest - 4 * m
        if r % 3 == 0 and (r == 0 or filler is not None):
            return (core + bytes((0xE0, 0x50, var, 0x00)) * m
                    + bytes((0xE4, 0x00, filler or 0)) * (r // 3))
    raise ValueError('cannot fill %d bytes' % length)


def used_params(sec0, sec9):
    import diag_common as DC
    pl, ts, _te, _px = DC.parse_pages(sec9)
    used = {0}
    for _l, offs in DC.walk_layers(sec9, sec9.find(b'BACK'), ts):
        for o in offs:
            used.add(sec9[o + 26])
    for _a, o, op, z in _stream(sec0):
        if op in (0xE0, 0xE1):
            if sec0[o + 1] >> 4:
                raise ValueError('BGON/BGOFF with a variable area')
            used.add(sec0[o + 2])
        elif op in (0xE2, 0xE3, 0xE4):
            if sec0[o + 1]:
                raise ValueError('BG op with a variable area')
            used.add(sec0[o + 2])
    return used


def plan_field(name, parts, vparts, art, mb_cap=35.0, raw_cap=None):
    """(sec0, sec9, info) through ff7nx_palstates.plan_field, or raises."""
    import diag_common as DC
    import ff7nx_marginblack as MB
    import ff7nx_palstates as PS
    sec0 = parts[0]
    sec9 = parts[8]
    pages_l, _ts, _te, px = DC.parse_pages(sec9)
    if px != 768:
        raise ValueError('page size %d' % px)
    pm = {p.slot: p for p in pages_l if p is not None}
    effects = find_effects(sec0)
    if not effects:
        raise ValueError('no single-variable palette effect')
    cols, _hdr, npg, cpp = MB.palette_colours(parts[3])
    pal = cols.astype(np.int64).reshape(npg, cpp)
    used = used_params(sec0, sec9)
    free = [p for p in range(1, 64) if p not in used]
    new0 = bytearray(sec0)
    specs, notes, refused = [], [], []
    vars_done = set()
    tmp_used = set()
    for ef in effects:
        q = ef['q']
        try:
            tg = PS._targets(sec9, pm, q)
            vc = PS._vanilla_cells(tg, vparts[8])
            if ef['var'] in vars_done:
                raise ValueError('variable shared by two effects')
            uses = var_uses(sec0, ef['var'],
                            skip={o_ for o_, _l in ef['sites']})
            step, anchor, lo, hi, sets = lattice(sec0, uses)
            src = pal[ef['src_pal']]
            c5 = np.stack([src & 31, (src >> 5) & 31, (src >> 10) & 31], -1)
            # 1x cells as the build has them, per vanilla page
            byp = collections.defaultdict(list)
            for _l, _i, r in tg:
                sx, sy = struct.unpack_from('<hh', r, 14)
                a = np.frombuffer(pm[r[34]].data, np.uint8,
                                  count=65536).reshape(256, 256)
                byp[vc[r][0]].append((vc[r][1:], a[sy:sy + 16, sx:sx + 16]))
            k = px // PS.GRID
            # STATES: the lattice between the script's own bounds
            states = list(range(lo, hi + 1, step))
            n = len(states)
            if not 2 <= n <= MAX_MULTI:
                raise ValueError('%d states (%d..%d step %d)'
                                 % (n, lo, hi, step))
            render = {}
            for val in states:
                c = _apply(ef['op'], c5, ef['vo'], val)
                if ef['cp']:
                    c[:ef['cp'][1]] = c5[:ef['cp'][1]]
                cc = (c * 8).astype(np.float32)
                cc[0] = 0
                render[val] = cc
            key = [(v - 256 if (ef['op'] == 0xE9 and v >= 128) else v)
                   for v in states]
            s_all = [states[i] for i in np.argsort(key, kind='stable')]
            artmap = {}
            for vpage, cells in byp.items():
                recs = PS._frames(art, name, vpage, q)
                s_ord = list(s_all)
                spare = None
                if len(recs) == n - 1:
                    # the variable's starting value at one end of the range
                    # is never loaded (it changes before the first LDPAL):
                    # it shares its neighbour's frame
                    ends = [x for x in (s_all[0], s_all[-1]) if x in sets]
                    if len(ends) == 1:
                        spare = ends[0]
                        s_ord.remove(spare)
                if len(recs) > n or len(recs) < ALIGN_MIN * n:
                    raise ValueError('page %d: %d Cosmos frames for %d states'
                                     % (vpage, len(recs), n))
                xy = sorted({c for c, _a in cells})
                idx = {c: a for c, a in cells}
                fr = np.stack([PS._cells_of(art.provider, r, xy, px)
                               for r in recs])
                small = fr.reshape(len(recs), len(xy), 16, k // 16, 16,
                                   k // 16, 3).mean((3, 5))
                I = np.stack([idx[c] for c in xy])
                E = {v: render[v][I] for v in states}
                els = [float(E[v].mean()) for v in s_ord]
                if any(b < a - 1e-3 for a, b in zip(els, els[1:])) or \
                        els[-1] - els[0] < 1e-3:
                    raise ValueError('page %d: states not monotone in '
                                     'brightness' % vpage)
                cl = [float(f.mean()) for f in small]
                if any(b - a < 1e-3 for a, b in zip(sorted(cl),
                                                    sorted(cl)[1:])):
                    raise ValueError('page %d: Cosmos frames alike' % vpage)
                f_ord = list(np.argsort(cl))
                if len(f_ord) == len(s_ord):
                    owner = dict(zip(s_ord, f_ord))
                else:
                    owner = _align(s_ord, els, f_ord, sorted(cl))
                # alignment proof on the brighter half of the states: each
                # frame shows its state's picture, clearly better than the
                # same frame with its cells rolled by one
                chk = sorted(owner, key=lambda v: float(E[v].mean()))
                chk = chk[len(chk) // 2:]
                cs, ms = [], []
                for v in chk:
                    j = owner[v]
                    a = small[j].ravel()
                    b = E[v].ravel()
                    if a.std() < 1e-6 or b.std() < 1e-6:
                        raise ValueError('page %d: flat frame' % vpage)
                    cs.append(float(np.corrcoef(a, b)[0, 1]))
                    if len(xy) > 1:
                        ms.append(cs[-1] - float(np.corrcoef(
                            np.roll(small[j], 1, 0).ravel(), b)[0, 1]))
                if float(np.median(cs)) < MIN_CORR or \
                        (ms and float(np.median(ms)) < MIN_MARGIN):
                    raise ValueError('page %d: frames do not show the 1x '
                                     'picture (corr %.2f, margin %s)'
                                     % (vpage, float(np.median(cs)),
                                        round(float(np.median(ms)), 2)
                                        if ms else '-'))
                for v in s_all:
                    if v not in owner:      # a state Cosmos did not capture
                        i = s_all.index(v)
                        near = min((w for w in owner),
                                   key=lambda w: abs(s_all.index(w) - i))
                        owner[v] = owner[near]
                if spare is not None:
                    nb = s_all[1] if spare == s_all[0] else s_all[-2]
                    owner[spare] = owner[nb]
                for ci, c in enumerate(xy):
                    artmap[(vpage,) + c] = [fr[owner[v]][ci] for v in states]
            sites = ef['sites']
            if n <= MAX_STATES:
                if not free:
                    raise ValueError('no free background parameter')
                P = free.pop(0)
                new0 = bytearray(renumber(bytes(new0), uses, lo, step, n))
                for so, sl in sites:
                    new0[so:so + sl] = bg_fill(sl, P, ef['var'])
                specs.append({'palette': q, 'mode': 'levels', 'param': P,
                              'states': tuple(range(n)), 'art': artmap})
                how = 'param %d' % P
            else:
                block = _free_block(free, n)
                if block is None:
                    raise ValueError('no %d consecutive free parameters' % n)
                filler = [p for p in free if p not in block]
                prev = _free_var(bytes(new0), taken=vars_done | tmp_used)
                for p in block:
                    free.remove(p)
                new0 = bytearray(renumber(bytes(new0), uses, lo, step, n,
                                          base=block[0]))
                for so, sl in sites:
                    new0[so:so + sl] = bg_multi(sl, ef['var'], prev,
                                                filler[0] if filler else None)
                tmp_used.add(prev)
                specs.append({'palette': q, 'mode': 'levels',
                              'params': tuple(block),
                              'states': tuple(range(n)), 'art': artmap})
                how = 'params %d..%d, var5[%d] remembers the shown one' % (
                    block[0], block[-1], prev)
            vars_done.add(ef['var'])
            notes.append('palette %d: var5[%d] %d..%d step %d -> %d states, '
                         '%s' % (q, ef['var'], lo, hi, step, n, how))
        except Exception as exc:                               # noqa: BLE001
            refused.append('palette %d: %s' % (q, str(exc)[:70]))
    if not specs:
        raise ValueError('; '.join(refused) or 'nothing')
    sec0n, sec9n, info = PS.plan_field(name, parts, vparts, art, mb_cap,
                                       raw_cap, specs=specs,
                                       sec0=bytes(new0))
    info['auto'] = notes
    info['auto_refused'] = refused
    return sec0n, sec9n, info


def apply_to_flevel(archive, payloads, art, encode=None, mb_cap=35.0,
                    raw_cap=None, log=lambda *_: None):
    import lgp
    import ff7nx_palstates as PS
    total = {'names': [], 'refused': [], 'tried': 0}
    if disabled() or art is None or getattr(art, 'provider', None) is None:
        return total
    encode = encode or archive.encode_field
    want = _env_set(ONLY_ENV)
    skip = _env_set(SKIP_ENV) | EXCLUDE | set(PS.SPECS)
    for name in archive.names():
        low = name.lower()
        if want and low not in want:
            continue
        if low in skip:
            continue
        entry = archive.index.get(name)
        try:
            if entry is None or not archive.is_field(entry):
                continue
            p = payloads.get(name)
            van = archive.decompressed(entry)
            raw = lgp.lzs_decompress(p[4:]) if p else van
            parts = list(lgp.split_sections(raw))
            if parts[8].find(b'BACK') < 0:
                continue
            import diag_common as DC
            pl = DC.parse_pages(parts[8])[0]
            if not any(g is not None and g.depth == 1 and
                       PS.FX_LO <= g.slot < PS.FX_HI for g in pl):
                continue
            total['tried'] += 1
            vparts = list(lgp.split_sections(van))
            parts[0], parts[8], info = plan_field(name, parts, vparts, art,
                                                  mb_cap, raw_cap)
        except Exception as exc:                               # noqa: BLE001
            msg = str(exc)
            if not msg.startswith(('no single-variable', 'palette op',
                                   'LDPAL with')):
                total['refused'].append((name, msg[:90]))
            continue
        payloads[name] = encode(lgp.join_sections(parts))
        total['names'].append('%s (%s; %.1f MB)' % (
            name, ', '.join(info['auto']), info['mb']))
    return total


def summarise(st):
    out = []
    if st.get('names'):
        out.append('  PALETTE STATES AUTO (BUILD 618x): %d field(s) made '
                   'truecolor with their palette animation kept: %s. %s=1 '
                   'disables; SEVENTH_NX_PAL_AUTO_SKIP=a,b excludes fields.'
                   % (len(st['names']), ' | '.join(st['names']), OFF_ENV))
    if st.get('refused'):
        out.append('  palette states auto: %d field(s) left paletted (first: '
                   '%s)' % (len(st['refused']), '; '.join(
                       '%s: %s' % t for t in st['refused'][:6])))
    return '\n'.join(out)
