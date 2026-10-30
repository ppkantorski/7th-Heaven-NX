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

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import collections
import math
import os
import re
import struct

import numpy as np

# BUILD 671: in flevel.lgp's key (build.py), so a fast build cannot keep an
# archive made by an older reading.
VERSION = 859
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
# BUILD 671. ADPAL / MPPAL2 (E9 / EA) read their R operand's bank from the
# HIGH nibble of the third bank byte (x86 0x61B1BC: get_value(5, 8); bank
# index 5 = byte 3 >> 4, 0x60F750). Kujata's table (and this module before
# 671) read the low nibble, so a variable R was taken as a literal and the
# 1x render used for the proof had a saturated red. False keeps the old
# reading (for comparing builds).
R_BANK_HI = False


def _env_set(name):
    raw = os.environ.get(name, '').strip()
    return frozenset(x.strip().lower() for x in raw.split(',') if x.strip())


def disabled():
    return os.environ.get(OFF_ENV, '').strip().lower() in (
        '1', 'true', 'yes', 'on')


# ------------------------------------------------------------------ decode
def _dead_time_actor(sec0, a):
    """True when the routine at `a` is a RET owned only by an actor named
    ``Time``: Echo-S's FFNx day/night actor, which
    echo_s_flevel.disable_pc_time_cycle_actor turns into a no-op by writing
    RET over each of its entries and leaving the old body as orphaned bytes
    (they jump over inline data, so no decoder reads them). BUILD 713: that
    body made palauto refuse juninn, cosin3, elminn_1, holu_2 and losinn."""
    import field_dis as FD
    if sec0[a] != 0x00:
        return False
    _meta, names, entries = FD.parse(sec0)
    owners = [names[i] for i, row in enumerate(entries) if a in row]
    return bool(owners) and all(n.rstrip('\x00').strip() == 'Time'
                                for n in owners)


def _stream(sec0):
    import echo_s_flevel as ES
    out = []
    for a, b in ES._routine_blocks(sec0):
        st, cut = ES._decode_block(sec0, a, b)
        if cut:
            if not _dead_time_actor(sec0, a):
                raise ValueError('routine at %d does not decode' % a)
            st = [(a, 0x00, 1)]
        out.extend((a, o, op, z) for o, op, z in st)
    return out


def _value_op(s, o, op):
    """{'S','D','size','start','ch': [(bank, byte) x3 as B,G,R]}."""
    b12, b34, b5 = s[o + 1], s[o + 2], s[o + 3] & 15
    if op in (0xE9, 0xEA):
        if b12:
            raise ValueError('buffer from a variable')
        rb = (s[o + 3] >> 4) if R_BANK_HI else b5
        return {'S': s[o + 4], 'D': s[o + 5], 'start': 0,
                'ch': [(b34 >> 4, s[o + 6]), (b34 & 15, s[o + 7]),
                       (rb, s[o + 8])], 'size': s[o + 9] + 1}
    if op == 0xEF:
        # BUILD 671. ADPAL2 (x86 0x61B479): S, D literal at +4/+5; start
        # (bank 1 = byte1 hi), B (2 = byte1 lo), G (3 = byte2 hi), R (4 =
        # byte2 lo) at +6..+9; size (6 = byte3 lo) at +10. Adds like ADPAL,
        # entries start .. start+size-1 (mds7plr2's lights: 1..255).
        if (b12 >> 4) or b5:
            raise ValueError('ADPAL2 start/size from a variable')
        return {'S': s[o + 4], 'D': s[o + 5], 'start': s[o + 6],
                'ch': [(b12 & 15, s[o + 7]), (b34 >> 4, s[o + 8]),
                       (b34 & 15, s[o + 9])], 'size': s[o + 10] + 1}
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
    if op in (0xE9, 0xEF):
        sv = [x - 256 if x >= 128 else x for x in (r, g, b)]
        seg = seg + np.array(sv)[None]
    else:
        seg = (seg * np.array([r, g, b])[None]) >> MUL_SHIFT
    out[lo:hi] = np.clip(seg, 0, 31)
    return out


VALUE_OPS = (0xE9, 0xEA, 0xDF, 0xEF)      # ADPAL MPPAL2 MPPAL ADPAL2


# --------------------------------------------------------------- analysis
# BUILD 854. Effects the generic proof below cannot follow but that were
# proved by hand, keyed by the EXACT bytes of the routine that drives them
# (any other script -- another mod, another version -- does not match and
# gets the generic rules only).
#
# onna_4, entity lg23+ (+0x874..): palettes 12 and 14 blink through a shuttle
# that keeps each palette's original colours in palette 15 and buffer 0x20
# so the next ADPAL starts from them (FF7 has no "copy of the field's own
# palette" otherwise). Traced instruction by instruction, every pass loads
#     palette 12 = ADPAL(original 12, var5[5])   (FF..F7 step 2, 5 values)
#     palette 14 = ADPAL(original 14, var5[6])   (FF..F4 step 1, 12 values)
# and palette 15, the shuttle's store, is drawn by no record. The generic
# checks refuse it only because buffer 0x30 is also what palette 15 and the
# other palette load from. Cosmos ships 6 and 12 frames for them.
SHUTTLES = {
    'onna_4': (bytes.fromhex(
        'e5000e20ff240400e600200fffe5000c20ff240400e90055502030050505ffe6'
        '00300cff240100e7002030ffe5000f20ff240100e600300fff24010014500100'
        '0013145005f80207875005021005805001011011145005fe0307855005021005'
        '80500100e90055502030060606ffe600300eff240100e7002030ffe5000f20ff'
        '240100e600300fff240100145002000013145006f40207875006011005805002'
        '011011145006ff030785500601100580500200129e00'),
        # q: (offset of its ADPAL in the bytes above, source palette)
        {12: (0x15, 12), 14: (0x64, 14)}),
}


def _shuttle_effects(sec0, field, writers):
    spec = SHUTTLES.get((field or '').lower())
    if spec is None:
        return []
    sig, pals = spec
    at = sec0.find(sig)
    if at < 0 or sec0.find(sig, at + 1) >= 0:
        return []
    out = []
    for q, (rel, src) in sorted(pals.items()):
        o = at + rel
        if sec0[o] != 0xE9 or sec0[o + 10] != 0xE6 or \
                sec0[o + 13] != q or len(writers.get(q, ())) != 1:
            return []
        vo = _value_op(sec0, o, 0xE9)
        addrs = {lit for bk, lit in vo['ch'] if bk}
        if {bk for bk, _l in vo['ch'] if bk} != {5} or len(addrs) != 1:
            return []
        out.append({'q': q, 'src_pal': src, 'op': 0xE9, 'vo': vo,
                    'cp': None, 'var': addrs.pop(), 'sites': [(o, 15)]})
    return out


def find_effects(sec0, field=None):
    """[{q, src_pal, op, vo, var, sites: [(offset, length, cppal size)]}]
    for every palette whose every writer is `value op [CPPAL S->D] LDPAL`
    with one identical value op driven by one bank-5 variable. Raises for
    the field when a palette is written in a way this cannot follow.
    `field` admits the hand-proved SHUTTLES effects of that field too."""
    s = sec0
    st = _stream(s)
    writers = collections.defaultdict(list)
    for i, (_a, o, op, z) in enumerate(st):
        if op == 0xE6:
            if s[o + 1]:
                raise ValueError('LDPAL with a variable operand')
            writers[s[o + 3]].append(i)
        elif op in (0xEC, 0xE8, 0xEE, 0xED):
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
            if j < 0 or st[j][0] != a or st[j][2] not in VALUE_OPS:
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
            # BUILD 671: a CPPAL of entry 0 alone (the transparent key, which
            # the render zeroes anyway) may come from any buffer (pillar_1)
            if cp is not None and cp[0] != vo['S'] and cp[1] != 1:
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
               or (op3 in VALUE_OPS and s[o3 + 5] == vo['S'])
               for _a3, o3, op3, _z3 in st):
            continue
        # BUILD 714: the entries that matter are the ones every LDPAL loads
        # (nvmkin32 loads 128 and its ADPAL2 covers 1..127); past them the
        # palette keeps its own colours, which the render takes from the
        # source -- so a short load needs the source to be the palette
        load = min(s[st[i][1] + 4] + 1 for i in idx)
        if load < 256 and src_pal != q:
            continue
        if vo['start'] > 1 or vo['start'] + vo['size'] < load:
            # partial op (entry 0 is the key and may be skipped): the rest
            # of D must be a copy of S made in the same routine
            if not any(_a3 == a0 and op3 == 0xE7 and not s[o3 + 1]
                       and s[o3 + 2] == vo['S'] and s[o3 + 3] == vo['D']
                       and s[o3 + 4] + 1 >= load
                       for _a3, o3, op3, _z3 in st[:j0]):
                continue
        # D is read only by this palette's LDPALs
        readers = sum(1 for _a3, o3, op3, _z3 in st
                      if (op3 in (0xE6, 0xE7) and s[o3 + 2] == vo['D'])
                      or (op3 in VALUE_OPS and s[o3 + 4] == vo['D']))
        if readers != len(sites):
            continue
        effects.append({'q': q, 'src_pal': src_pal, 'op': vop, 'vo': vo,
                        'cp': cp, 'var': addrs.pop(),
                        'sites': [(o_, l_) for o_, l_, _a, _j in sites]})
    have = {e['q'] for e in effects}
    effects += [e for e in _shuttle_effects(s, field, writers)
                if e['q'] not in have]
    return effects


def _refs(sec0, o, op, z):
    """[(operand offset, bank, addresses covered)] for the variable operands
    of one instruction; None when its layout is unknown."""
    import ff7nx_opbanks as OB
    if op == 0x28 and z >= 3 and sec0[o + 1] == z:
        # BUILD 854. KAWAI (the model's eye/mouth/light sub-opcodes the
        # facial animation and Echo-S script): length-prefixed, and every
        # parameter is a literal -- the handler (x86 0x620136) stores the
        # sub-opcode and a pointer to the bytes for the model loop and reads
        # no variable bank. Unknown before, so any KAWAI carrying the effect
        # variable's index as a byte refused the whole effect (onna_4's
        # blinking lights and 18 others).
        return []
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
        if (op in (0x14, 0x15) and len(hit) == 1 and len(refs) == 2
                and hit[0][0] in (2, 3)):
            # BUILD 671: v against a bank-5 byte that only ever holds one
            # literal (mds7plr2: IF v > var5[3] with var5[3] = F0 once)
            other = [(k_, b_, c_) for k_, b_, c_ in refs if k_ != hit[0][0]]
            if other and other[0][1] == 5:
                cv = const_var(s, s[o + other[0][0]], v)
                tag = 'const'
                if cv is None:
                    # BUILD 714: the bound is also compared against other
                    # variables (nvmkin32: var5[19]/var5[20] bound four
                    # loops) -- this comparison takes the literal instead,
                    # and var5[w] stays as it is for the others
                    cv = const_var(s, s[o + other[0][0]], v, loose=True)
                    tag = 'lit'
                if cv is not None:
                    k = hit[0][0]
                    out.append((o, 'ifl' if k == 2 else 'ifr',
                                (tag, s[o + other[0][0]]) + cv))
                    continue
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


def const_var(sec0, w, v, loose=False):
    """(literal, (SET offsets,)) when var5[w] is only ever SET to that one
    literal and otherwise only compared (IFUB/IFUBL) against var5[v]; None
    otherwise. Unknown layouts carrying the byte, word access, any other
    write or read: None."""
    s = sec0
    lits, sets = set(), []
    for _a, o, op, z in _stream(s):
        refs = _refs(s, o, op, z)
        if refs is None:
            if w in s[o + 1:o + z]:
                return None
            continue
        mine = [(k, b) for k, b, cover in refs if b in (5, 6) and w in cover]
        if not mine:
            continue
        if any(b == 6 for _k, b in mine) or len(mine) > 1:
            return None
        k = mine[0][0]
        if op == 0x80 and k == 2 and len(refs) == 1:
            lits.add(s[o + 3])
            sets.append(o)
        elif op in (0x14, 0x15) and len(refs) == 2 and all(
                b == 5 for _k, b, _c in refs) and \
                (s[o + (3 if k == 2 else 2)] == v or loose) and w != v and \
                s[o + (3 if k == 2 else 2)] != w:
            continue
        else:
            return None
    if len(lits) != 1 or not sets:
        return None
    return lits.pop(), tuple(sets)


def _sv(x):
    return x - 256 if x >= 128 else x


def _frames(art, name, vpage, q, used=None):
    """Cosmos's state frames for (field, page, palette): the installed
    folders' first; BUILD 717, else frames that live only in a folder the
    mod does not install (spipe_1's pipe light, in AA REMOVED), so the
    effect keeps every state instead of staying 1997 art."""
    import ff7nx_palstates as PS
    recs = PS._frames(art, name, vpage, q)
    if recs:
        return recs
    alt = getattr(art.provider, 'inactive_state_slots', {}).get(
        (name.lower(), vpage, q)) or ()
    if alt and used is not None:
        used.add(q)
    return list(alt)


def lattice(sec0, uses, dead=(), signed=False):
    """(step, anchor, lo, hi): the values the variable can hold when the
    palette is loaded, from its own instructions.

    A comparison bounds the range according to what it guards (IFUB runs
    the next instruction only when true):
      v < c (or <=) guarding PLUS d   -> the top is the last value passing + d
      v > c (or >=) guarding MINUS d  -> the bottom is the first passing - d
      v > c (or >=) guarding SETBYTE  -> a reset: values that pass are reset
                                         before they are shown (top = last
                                         value failing), and mirrored for <
      anything else (==, !=, guarding another variable) -> c itself.

    BUILD 716: signed=True reads the variable as a signed byte, for an
    ADPAL brightness that wraps through 0 (junonr1: 0, MINUS 1 -> 255 ..
    0xF6, PLUS back to 0 is -10..0). Only wrapping PLUS/MINUS and ==/!=
    comparisons qualify, so the signed reading is exact."""
    s = sec0
    if signed:
        for o, kind, k in uses:
            if kind in ('plus', 'minus') and s[o] not in (0x85, 0x87):
                raise ValueError('signed lattice: saturating op %02X' % s[o])
            if kind in ('inc', 'dec'):
                raise ValueError('signed lattice: INC/DEC')
            if kind in ('ifl', 'ifr') and s[o + 4] not in (0, 1):
                raise ValueError('signed lattice: comparison %d' % s[o + 4])
    sv = _sv if signed else (lambda x: x)
    steps, sets = [], []
    for o, kind, k in uses:
        if kind in ('plus', 'minus'):
            steps.append(s[o + 3])
        elif kind in ('inc', 'dec'):
            steps.append(1)
        elif kind == 'set' and o not in dead:
            sets.append(sv(s[o + 3]))
    steps = [x for x in steps if x]
    if not steps and len(set(sets)) >= 2:
        # BUILD 713: a flicker SETs the variable to one of a few literals
        # and never steps it (junpb_1's sign: 0x30 / 0x80; juninn's lamps;
        # mtcrl_1). The lattice is the gcd of their differences.
        steps = [abs(x - sets[0]) for x in sets if x != sets[0]]
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
        c, cmp = (k[2] if isinstance(k, tuple) else s[o + k]), s[o + 4]
        c = sv(c)
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
    if signed and (lo < -128 or hi > 127):
        raise ValueError('range %d..%d leaves a signed byte' % (lo, hi))
    if not signed and (lo < 0 or hi > 255):
        raise ValueError('range %d..%d leaves a byte' % (lo, hi))
    return step, anchor, lo, hi, [x & 255 for x in sets]


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


def renumber(sec0, uses, lo, step, n, base=0, dead=(), signed=False):
    """Section 0 with var5[v] renumbered to base + (v - lo) / step
    (v read as a signed byte when `signed`, BUILD 716)."""
    sv = _sv if signed else (lambda x: x)
    out = bytearray(sec0)
    for o, kind, k in uses:
        if kind == 'set' and o in dead:
            out[o + 3] = base
        elif kind == 'set':
            c = sv(sec0[o + 3])
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
            lit = k[2] if isinstance(k, tuple) else sec0[o + k]
            c2 = _cmp_map(sv(lit), cmp, lo, step,
                          'l' if kind == 'ifl' else 'r')
            if c2 is None or not 0 <= base + c2 <= 255:
                raise ValueError('comparison %d with %d cannot be kept'
                                 % (cmp, lit))
            if isinstance(k, tuple) and k[0] == 'lit':
                # BUILD 714: compare against the literal; v's own bank stays
                if kind == 'ifl':
                    out[o + 1] = sec0[o + 1] & 0xF0
                    out[o + 3] = base + c2
                else:
                    out[o + 1] = sec0[o + 1] & 0x0F
                    out[o + 2] = base + c2
            elif isinstance(k, tuple):
                # the bound lives in var5[w]: rewrite its SET literal(s),
                # which must agree across every comparison against it
                for so in k[3]:
                    if out[so + 3] != sec0[so + 3] and \
                            out[so + 3] != base + c2:
                        raise ValueError('var5[%d] bound maps two ways' % k[1])
                    out[so + 3] = base + c2
            else:
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


_BG_ON, _BG_OFF, _BG_ROL, _BG_ROL2, _BG_CLR = 0xE0, 0xE1, 0xE2, 0xE3, 0xE4


def init_offsets(sec0):
    """Offsets of every instruction in an actor's init (script 0 up to its
    first RET)."""
    import field_dis as FD
    import echo_s_flevel as ES
    _meta, _names, entries = FD.parse(sec0)
    out = set()
    for row in entries:
        o = row[0]
        while o < len(sec0) and sec0[o] != 0x00:
            try:
                z = ES.instruction_size(sec0, o)
            except Exception:                                  # noqa: BLE001
                break
            if z <= 0:
                break
            out.add(o)
            o += z
    return out


def dead_init_sets(sec0, uses, sites):
    """BUILD 713. Offsets of SETs of a never-stepped flicker variable that
    only initialise it: they sit in an actor's init, the variable is SET to
    at least two other literals outside any init, and in the routine that
    loads the palette such a SET comes before the load. juninn: var5[2] = 0
    at init, then 37 or 48 before every MPPAL. The value they write is never
    loaded, so they are left out of the states (and mapped to state 0)."""
    import echo_s_flevel as ES
    if any(kind in ('plus', 'minus', 'inc', 'dec') for _o, kind, _k in uses):
        return set()
    inits = init_offsets(sec0)
    dead = {o for o, kind, _k in uses if kind == 'set' and o in inits}
    main = [(o, sec0[o + 3]) for o, kind, _k in uses
            if kind == 'set' and o not in inits]
    if not dead or len({v for _o, v in main}) < 2:
        return set()
    blocks = ES._routine_blocks(sec0)
    for so, _sl in sites:
        blk = [(a, b) for a, b in blocks if a <= so < b]
        if not blk or not any(blk[0][0] <= o < so for o, _v in main):
            return set()
    return dead


def always_on(sec0, sec9, q):
    """(param, masks) for palette q's FX records when they carry a
    background parameter that the script only ever switches ON, in an
    actor's init (script 0 before its first RET) -- so after the first frame
    the records are always drawn and their copies can be param 0's. (0, None)
    when the records are param 0. Raises on anything else. BUILD 713:
    juninn's lamps (param 2, one bit per lamp, BGON in LIGHT/LIGHT2's init)."""
    import ff7nx_palstates as PS
    import field_dis as FD
    import echo_s_flevel as ES
    got = set()
    for _layer, _c, first, n in PS._layer_rows(sec9):
        for i in range(n):
            r = sec9[first + i * PS.REC:first + (i + 1) * PS.REC]
            if r[28] and r[22] == q and PS.FX_LO <= r[34] < PS.FX_HI:
                got.add((r[26], r[27]))
    params = {p for p, _m in got}
    if params == {0} or not params:
        return 0, None
    if len(params) != 1:
        raise ValueError('palette %d records span params %s'
                         % (q, sorted(params)))
    P = params.pop()
    masks = {m for _p, m in got}
    inits = init_offsets(sec0)
    on = set()
    for _a, o, op, z in _stream(sec0):
        if op not in (_BG_ON, _BG_OFF, _BG_ROL, _BG_ROL2, _BG_CLR):
            continue
        if sec0[o + 1]:
            raise ValueError('BGON/BGOFF with a variable operand')
        if sec0[o + 2] != P:
            continue
        if op != _BG_ON:
            raise ValueError('param %d is switched off by the script' % P)
        if o not in inits:
            raise ValueError('param %d is switched on outside an init' % P)
        on.add(1 << sec0[o + 3])
    if not on or any(m not in on for m in masks):
        raise ValueError('param %d states %s not switched on at init'
                         % (P, sorted(masks)))
    return P, masks


GATE_WHY = ('is switched off by the script', 'is switched on outside an init')


def gate_of(sec0, sec9, q):
    """BUILD 716. (P, masks, ops) for palette q's FX records when the script
    switches their background parameter on and off mid-scene (zz7's machine
    light: BGON 2,0 while var5[2] == 1, BGOFF 2,0 otherwise). One parameter,
    one state bit, constant operands, no BGROL. `ops` are [(offset, size,
    'on' | 'off')] of every BGON / BGOFF of that bit and BGCLR of that
    parameter; the converted copies follow them through a gate variable."""
    import ff7nx_palstates as PS
    got = set()
    for _layer, _c, first, n in PS._layer_rows(sec9):
        for i in range(n):
            r = sec9[first + i * PS.REC:first + (i + 1) * PS.REC]
            if r[28] and r[22] == q and PS.FX_LO <= r[34] < PS.FX_HI:
                got.add((r[26], r[27]))
    params = {p for p, _m in got}
    if len(params) != 1 or 0 in params:
        raise ValueError('palette %d records span params %s'
                         % (q, sorted(params)))
    P = params.pop()
    masks = {m for _p, m in got}
    if len(masks) != 1 or bin(next(iter(masks))).count('1') != 1:
        raise ValueError('gated palette %d: masks %s' % (q, sorted(masks)))
    b = next(iter(masks)).bit_length() - 1
    ops = []
    for _a, o, op, z in _stream(sec0):
        if op not in (_BG_ON, _BG_OFF, _BG_ROL, _BG_ROL2, _BG_CLR):
            continue
        if sec0[o + 1]:
            raise ValueError('BGON/BGOFF with a variable operand')
        if sec0[o + 2] != P:
            continue
        if op in (_BG_ROL, _BG_ROL2):
            raise ValueError('gated param %d is rolled' % P)
        if op == _BG_CLR:
            ops.append((o, z, 'off'))
        elif sec0[o + 3] == b:
            ops.append((o, z, 'on' if op == _BG_ON else 'off'))
    if not any(k == 'on' for _o, _z, k in ops):
        raise ValueError('gated param %d never switched on' % P)
    return P, masks, ops


def gate_codes(mode, var, P=None, prev=None, tb=None, pb=None):
    """(show, hide): the bytes that put the copies of the current state on
    the screen and take them all off, per conversion mode."""
    if mode == 'single':
        return (bytes((0xE4, 0x00, P, 0xE0, 0x05, P, var)),
                bytes((0xE4, 0x00, P)))
    if mode == 'multi':
        return (bytes((0xE1, 0x50, prev, 0x00, 0xE0, 0x50, var, 0x00,
                       0x80, 0x55, prev, var)),
                bytes((0xE1, 0x50, prev, 0x00)))
    return (pair_code(var, prev, tb, pb),
            bytes((0xE4, 0x05, prev, 0xE4, 0x00, pb)))


def gate_site(vv, hide):
    """Appended to an update site: IF var5[vv] == 0 (gate shut) hide."""
    return bytes((0x14, 0x50, vv, 0x00, 0x00, 1 + len(hide))) + hide



# ------------------------------------------------- BUILD 714: pair layers
# nvmkin32: four palettes fade through 19 ADPAL2 steps over 157 tiles. One
# truecolor copy per state is 2983 cells; the FX band and the field's
# texture budget hold ~1530. PAIR mode keeps every state with 11 images per
# tile: state i shows A[i // 2] plus, on odd i, a fine image B; FX records
# are additive, so they draw as their sum. The script picks parameter
# block[i // 4] and state bit i % 4 (BGON var5[q],var5[r]), so A[2q] and
# A[2q+1] are the bits {0,1} / {2,3} of one parameter and B one more
# parameter's bits {1,3}. Where even that does not fit, the tiles whose fit
# loses least take A[i // 4] + two bit planes (7 images). All images are a
# non-negative least-squares fit to Cosmos's frames, refused past
# PAIR_MAX_ERR (255 scale, 99.9th percentile of every texel and state).
PAIR_MAX_ERR = 10.0
PAIR_MAX_MEAN = 0.6
PAIR_K4_STEPS = (0, 20, 40, 60, 80, 100, 130, 160)
OP_DIV, OP_MOD, OP_NOP = 0x8B, 0x8D, 0x5F
# jump opcodes: (operand width, backward); the operand is the instruction's
# last byte(s). Forward targets are operand address + value, backward ones
# the opcode's own offset - value.
_JUMPS = {0x10: (1, False), 0x11: (2, False), 0x12: (1, True),
          0x13: (2, True), 0x14: (1, False), 0x15: (2, False),
          0x16: (1, False), 0x17: (2, False), 0x18: (1, False),
          0x19: (2, False), 0x30: (1, False), 0x31: (1, False),
          0x32: (1, False), 0xCB: (1, False), 0xCC: (1, False)}


def _bits(r, nbits):
    return [(r >> j) & 1 for j in range(nbits)]


def pair_fit(frames, k=2, iters=12):
    """frames[i] ~ A[i // k] + the sum of L[j] over the bits j of i % k
    (k = 2: one fine image; k = 4: two bit planes), every image
    non-negative and the sum clipped at 255. (A, L, mean err, p99.9 err)."""
    F = np.stack(frames).astype(np.float32)
    n = len(F)
    na = (n + k - 1) // k
    nb = (k - 1).bit_length()
    A = np.stack([F[k * a] for a in range(na)])
    L = np.zeros((nb,) + F.shape[1:], np.float32)

    def low(i, skip=None):
        bs = _bits(i % k, nb)
        return sum((L[j] for j in range(nb) if bs[j] and j != skip),
                   np.zeros(F.shape[1:], np.float32))
    for _ in range(iters):
        for j in range(nb):
            d = [F[i] - A[i // k] - low(i, j) for i in range(n)
                 if _bits(i % k, nb)[j]]
            L[j] = np.clip(np.mean(d, 0), 0, 255)
        for a in range(na):
            d = [F[i] - low(i) for i in range(k * a, min(k * a + k, n))]
            A[a] = np.clip(np.mean(d, 0), 0, 255)
    R = np.stack([np.clip(A[i // k] + low(i), 0, 255) for i in range(n)])
    e = np.abs(R - F)
    return list(A), list(L), float(e.mean()), float(np.percentile(e, 99.9))


# BUILD 854: at most this many tiles of one effect may fall back to one
# image per state inside pair mode (exact_records)
PAIR_EXACT_MAX = 4


def exact_records(frames, block):
    """[(param, state bits, image)] showing frame i alone: parameter
    block[i // 4], state bit i % 4 -- the same switch pair mode drives, so
    the tile follows the effect exactly."""
    return [(block[i // 4], 1 << (i % 4), img)
            for i, img in enumerate(frames)]


def pair_records(fit, k, block, pb):
    """[(param, state bits, image)]: q = i // 4 picks the parameter
    block[q], r = i % 4 the state bit (BGON var5[q], var5[r])."""
    A, L = fit[0], fit[1]
    out = []
    if k == 2:
        for a, img in enumerate(A):
            out.append((block[a // 2], 0b0011 if a % 2 == 0 else 0b1100,
                        img))
        out.append((pb, 0b1010, L[0]))
    else:
        for a, img in enumerate(A):
            out.append((block[a], 0b1111, img))
        out.append((pb, 0b1010, L[0]))
        out.append((pb, 0b1100, L[1]))
    return out


def _offset(var, delta):
    """PLUS / MINUS var5[var], |delta| (4 bytes; empty for 0)."""
    if not delta:
        return b''
    return bytes((0x85 if delta > 0 else 0x87, 0x50, var, abs(delta)))


def pair_code(var, prev, tb, pb, delta=0):
    """BGCLR var5[prev]; prev = v / 4 (+ delta); tb = v % 4;
    BGON var5[prev],var5[tb]; BGCLR pb; BGON pb,var5[tb] -- 30 bytes (34
    with an offset), no jump, no wait."""
    return (bytes((0xE4, 0x05, prev, 0x80, 0x55, prev, var,
                   OP_DIV, 0x50, prev, 0x04)) + _offset(prev, delta)
            + bytes((0x80, 0x55, tb, var, OP_MOD, 0x50, tb, 0x04,
                     0xE0, 0x55, prev, tb, 0xE4, 0x00, pb, 0xE0, 0x05, pb,
                     tb)))


def multi_follow(length, var, prev, delta):
    """A follower in one-parameter-per-state mode: BGOFF var5[prev];
    prev = v + delta; BGON var5[prev], padded with BGON var5[prev] / NOP."""
    core = (bytes((0xE1, 0x50, prev, 0x00, 0x80, 0x55, prev, var))
            + _offset(prev, delta) + bytes((0xE0, 0x50, prev, 0x00)))
    if len(core) > length:
        raise ValueError('cannot fit a follower in %d bytes' % length)
    rest = length - len(core)
    return (core + bytes((0xE0, 0x50, prev, 0x00)) * (rest // 4)
            + bytes((OP_NOP,)) * (rest % 4))


def grow(sec0, edits):
    """Section 0 with each (offset, old length, new bytes) applied (BUILD
    858: shorter, or a zero-length insertion, too): routine entries, the string offset, the AKAO
    table and every jump across a grown site move with the code. Raises if
    a jump lands inside a site, or a site holds a jump."""
    import echo_s_flevel as ES
    edits = sorted(edits)
    st = _stream(sec0)
    for (o1, l1, _b1), (o2, _l2, _b2) in zip(edits, edits[1:]):
        if o1 + l1 > o2:
            raise ValueError('sites overlap')

    def moved(x):
        return x + sum(len(b) - l for o, l, b in edits if o + l <= x)

    def inside(x):
        return any(o < x < o + l for o, l, _b in edits)
    fix = []
    for _a, o, op, z in st:
        if op not in _JUMPS:
            continue
        w, back = _JUMPS[op]
        at = o + z - w
        val = sec0[at] if w == 1 else struct.unpack_from('<H', sec0, at)[0]
        tgt = o - val if back else at + val
        if inside(o) or inside(tgt):
            raise ValueError('jump at %d into or inside a site' % o)
        # (BUILD 858: a zero-length insertion may sit right before a jump)
        if any(o <= e[0] < o + z and not (e[1] == 0 and e[0] == o)
               for e in edits):
            raise ValueError('site starts inside a jump at %d' % o)
        nval = (moved(o) - moved(tgt)) if back else (moved(tgt) - moved(at))
        if nval != val:
            if not 0 <= nval < (256 if w == 1 else 65536):
                raise ValueError('jump at %d no longer reaches' % o)
            fix.append((at, w, nval))
    out = bytearray(sec0)
    for at, w, nval in fix:
        if w == 1:
            out[at] = nval
        else:
            struct.pack_into('<H', out, at, nval)
    hdr = ES._SECTION1_FIXED_HEADER
    nact = sec0[2]
    nak = struct.unpack_from('<H', sec0, 6)[0]
    so = struct.unpack_from('<H', sec0, 4)[0]
    if any(o + l > so for o, l, _b in edits):
        raise ValueError('site past the script')
    rt = hdr + nact * 8 + nak * 4
    for i in range(nact * ES._SECTION1_ROUTINES_PER_ACTOR):
        e = struct.unpack_from('<H', sec0, rt + 2 * i)[0]
        if inside(e):
            raise ValueError('routine starts inside a site')
        struct.pack_into('<H', out, rt + 2 * i, moved(e))
    for i in range(nak):
        at = hdr + nact * 8 + 4 * i
        struct.pack_into('<I', out, at,
                         moved(struct.unpack_from('<I', sec0, at)[0]))
    new_so = moved(so)
    if new_so > 0xFFFF:
        raise ValueError('string offset overflows')
    struct.pack_into('<H', out, 4, new_so)
    for o, l, b in sorted(edits, reverse=True):
        out[o:o + l] = b
    out = bytes(out)
    ES.validate_akao_offsets(out)
    return out


# ------------------------------------------- BUILD 858: the tick budget
# The field interpreter gives an actor 8 opcodes a tick (ff7nx_ticksim).
# onna_4's lg23+ steps var5[6] and loads palette 14 in one tick of exactly
# 8 opcodes (IF IF MINUS SKIP SKIP ADPAL LDPAL WAIT); BUILD 854's 8-opcode
# pair switch ran out after its BGCLR, so palette 14's lamps went dark for
# one tick every pass and each pass took a tick longer (hardware 10-28,
# "lights that are rapidly flickering"). Every rewritten site now has the
# vanilla site's opcode count, its visible switch last (where LDPAL was),
# and the whole field is proven against the vanilla script in the tick
# model before it ships (tick_proof): the state on screen must match
# vanilla's palette state on every tick.
TICK_TICKS = 1200
TICK_WHY = 'tick proof'
RELAXED_RATE = 0.6
RELAXED_LAG = 2
ONE_COPY_MB_ENV = 'SEVENTH_NX_PAL_AUTO_MB'
ONE_COPY_MB = 40.0


def one_copy_mb_cap():
    try:
        return float(os.environ.get(ONE_COPY_MB_ENV) or ONE_COPY_MB)
    except ValueError:
        return ONE_COPY_MB
STATIC_TOL = 4.0          # palstates: a tile within this of one image


def _ops_in(stream, so, sl):
    return [(o, op, z) for _a, o, op, z in stream if so <= o < so + sl]


def site_single(kv, P, var):
    """BGCLR P; BGON P,var5[var] behind NOPs to `kv` opcodes."""
    if kv < 2:
        raise ValueError('a %d-opcode site cannot switch' % kv)
    return (bytes((OP_NOP,)) * (kv - 2)
            + bytes((0xE4, 0x00, P, 0xE0, 0x05, P, var)))


def site_multi(kv, var, prev, inline=False):
    """(site bytes, deferred bytes): BGOFF var5[prev]; BGON var5[var];
    var5[prev] = var5[var] behind NOPs to `kv` opcodes. A 2-opcode site
    keeps the switch and defers the bookkeeping (the SET) to after the
    next WAIT -- the tick the vanilla script spends on a WAIT has room."""
    off = bytes((0xE1, 0x50, prev, 0x00))
    on = bytes((0xE0, 0x50, var, 0x00))
    keep = bytes((0x80, 0x55, prev, var))
    if kv >= 3:
        return bytes((OP_NOP,)) * (kv - 3) + off + on + keep, b''
    if kv == 2 and inline:
        # no WAIT to defer to (a loop paced by its BACK), or the deferred
        # SET failed the proof: one opcode more in the site's own tick
        return off + on + keep, b''
    if kv == 2:
        return off + on, keep
    raise ValueError('a %d-opcode site cannot switch' % kv)


def _after_next_wait(stream, so, at):
    """Offset after the first WAIT at or past `at` in the routine of the
    instruction at `so`."""
    rt = next(a for a, o, _op, _z in stream if o == so)
    for a, o, op, z in stream:
        if a == rt and o >= at and op == 0x24:
            return o + z
    return None


MULTI_VARIANTS = 4


def _multi_variant(stream, sec0, so, sl, kv, var, prev, n):
    """The edits for one multi-mode site, variant `n` (tried in order until
    tick_proof passes):
      0  BGOFF; BGON at the site, the SET after the next WAIT (needs the
         variable unchanged until then);
      1  all three at the site, and the next WAIT one tick shorter (gone
         if it was 1) -- the vanilla tick was full, so its WAIT slips a
         tick and this gives the tick back (mds7plr2, nvmkin32);
      2  all three at the site;
      3  the same, held to the RELAXED proof (tick_proof(relaxed=True)):
         every state, in order, never a torn or empty tick, but the pass
         may run a tick longer -- the last resort before staying paletted,
         for a loop whose vanilla tick is full on some paths and not on
         others (mds7plr2, nvmkin32).
    A 3+-opcode site has one form."""
    if kv >= 3:
        return [(so, sl, site_multi(kv, var, prev)[0])]
    wat = _after_next_wait(stream, so, so + sl)
    if n == 0 and wat is not None:
        code, later = site_multi(kv, var, prev)
        return [(so, sl, code), (wat, 0, later)]
    code = site_multi(kv, var, prev, inline=True)[0]
    out = [(so, sl, code)]
    if n == 1 and wat is not None:
        wo = wat - 3
        w = struct.unpack_from('<H', sec0, wo + 1)[0]
        out.append((wo, 3, bytes((0x24,)) + struct.pack('<H', w - 1)
                    if w >= 2 else b''))
    return out


def _mover(edits):
    def moved(x):
        return x + sum(len(b) - l for o, l, b in edits if o + l <= x)
    return moved


def _decode(pr, bg):
    """The state index the background shows for one proof record."""
    mode = pr['mode']
    if mode == 'single':
        bits = bg.get(pr['P'], 0)
        if bits and bits & (bits - 1) == 0:
            return bits.bit_length() - 1
        return None
    blk = pr['block']
    on = [p for p in blk if bg.get(p, 0)]
    if mode == 'multi':
        if len(on) == 1 and bg[on[0]] == 1:
            return on[0] - blk[0]
        return None
    if len(on) != 1:
        return None
    bits = bg[on[0]]
    if bits & (bits - 1) or bg.get(pr['pb'], 0) != bits:
        return None
    return 4 * (on[0] - blk[0]) + bits.bit_length() - 1


def tick_proof(vsec0, nsec0, edits, pr, ticks=TICK_TICKS, relaxed=False):
    """None when, on every tick, the rewritten script shows the state the
    vanilla script's palette shows; else why not. 'unverified: ...' when
    the tick model cannot follow the actors. `relaxed`: the same states in
    the same order with no empty or torn tick, at no less than RELAXED_RATE
    of vanilla's pace (a pass a tick longer)."""
    import ff7nx_ticksim as TS
    moved = _mover(edits)
    s0v = TS.script0_entries(vsec0)
    vstarts, nstarts = set(), set()
    for so, _sl in pr['sites']:
        rv = TS.routine_of(vsec0, so)
        if rv is None or rv[0] not in s0v:
            return 'unverified: site outside a script 0'
        vstarts.add(rv[0])
        nstarts.add(moved(rv[0]))
    s0n = TS.script0_entries(nsec0)
    vt = TS.run_many(vsec0, vstarts, ticks, watch=[(5, pr['var'])],
                     inits=set(s0v))
    nt = TS.run_many(nsec0, nstarts, ticks, inits=set(s0n))
    if vt is None or nt is None:
        return 'unverified: the tick model cannot follow the actors'
    stream = _stream(vsec0)
    first = {so: so for so, _sl in pr['sites']}
    last = {}
    for so, sl in pr['sites']:
        ops = _ops_in(stream, so, sl)
        last[ops[-1][0]] = so
    idx = {v: i for i, v in enumerate(pr['states'])}
    cur, pend = None, {}
    want = []
    for ran, _bg in vt:
        for _ai, o, _op, vals in ran:
            if o in first:
                pend[o] = vals[0]
            if o in last:
                v = pend.get(last[o])
                if v in idx:
                    cur = idx[v]
        want.append(cur)
    got = [_decode(pr, bg) for _ran, bg in nt]
    t0 = next((t for t, w in enumerate(want) if w is not None), None)
    if t0 is None:
        return 'unverified: the vanilla actors never load the palette'
    if relaxed:
        if any(g is None for g in got[t0 + RELAXED_LAG:]):
            return 'relaxed: an empty or torn tick'
        ws = [w for i, w in enumerate(want[t0:]) if i == 0 or
              w != want[t0 + i - 1]]
        gs = [g for i, g in enumerate(got[t0 + RELAXED_LAG:])
              if i == 0 or g != got[t0 + RELAXED_LAG + i - 1]]
        if gs[1:] != ws[1:len(gs)] or len(gs) < RELAXED_RATE * len(ws):
            return 'relaxed: states out of order or too slow'
        return None
    for t in range(t0, ticks):
        if got[t] != want[t]:
            return ('tick %d shows state %s, vanilla %s'
                    % (t, got[t], want[t]))
    return None


def plan_field(name, parts, vparts, art, mb_cap=35.0, raw_cap=None):
    """(sec0, sec9, info) through ff7nx_palstates.plan_field, or raises.
    BUILD 716: script-switched layers are followed (gate_of); a field where
    that would leave another FX palette paletted is planned exactly as
    build 714 planned it (its switched layer refused)."""
    exclude, variant = set(), {}
    while True:
        opts = (frozenset(exclude), tuple(sorted(variant.items())))
        try:
            try:
                return _plan_all(name, parts, vparts, art, mb_cap, raw_cap,
                                 True, opts)
            except ValueError as exc:
                if not str(exc).startswith(PARTIAL):
                    raise
            return _plan_all(name, parts, vparts, art, mb_cap, raw_cap,
                             False, opts)
        except ValueError as exc:
            # BUILD 858: a palette whose rewrite fails the proof tries the
            # next site variant (_multi_variant); one that fails them all
            # stays paletted, and the field's other effects still go
            m = re.search(TICK_WHY + r': palette (\d+)', str(exc))
            if m is None:
                raise
            q = int(m.group(1))
            if variant.get(q, 0) < MULTI_VARIANTS - 1:
                variant[q] = variant.get(q, 0) + 1
            elif q not in exclude:
                exclude.add(q)
            else:
                raise


PARTIAL = 'switched layer converts but not the rest'


def _plan_all(name, parts, vparts, art, mb_cap, raw_cap, gates,
              opts=(frozenset(), frozenset())):
    """BUILD 714: when one copy per state does not fit (FX band, texture
    budget), the multi-parameter effects are tried again in PAIR mode."""
    try:
        return _plan(name, parts, vparts, art, mb_cap, raw_cap, False,
                     gates=gates, opts=opts)
    except ValueError as exc:
        msg = str(exc)
        if 'do not fit the FX band' not in msg and 'MB cap' not in msg \
                and 'binding cap' not in msg and 'shared variable' not in msg \
                and TICK_WHY not in msg:
            raise
        # BUILD 858: one copy per state keeps vanilla's timing exactly
        # (pair mode's 8-opcode switch rarely fits a tick), so it gets
        # the user's 40 MB (10-28: "35 mb is more of a rough
        # recommendation ... our worst field actually hits 40 mb without
        # issues") before pair mode is tried
        big = one_copy_mb_cap()
        if 'MB cap' in msg and big > mb_cap:
            try:
                return _plan(name, parts, vparts, art, big, raw_cap, False,
                             gates=gates, opts=opts)
            except ValueError as exc3:
                msg3 = str(exc3)
                if 'MB cap' not in msg3 and 'FX band' not in msg3 and \
                        'binding cap' not in msg3 and TICK_WHY not in msg3:
                    raise
        last = None
        for k4 in PAIR_K4_STEPS:
            try:
                return _plan(name, parts, vparts, art, mb_cap, raw_cap, True,
                             k4, gates=gates, opts=opts)
            except ValueError as exc2:
                last = str(exc2)
                if last.startswith(PARTIAL):
                    raise
                if 'do not fit the FX band' not in last and \
                        'MB cap' not in last and 'binding cap' not in last:
                    break
        raise ValueError('%s; pair mode: %s' % (msg, last)) from None


def _plan(name, parts, vparts, art, mb_cap, raw_cap, pair, k4=0, gates=True,
          opts=(frozenset(), frozenset())):
    """plan_field's work; `pair` switches multi-parameter effects to PAIR
    mode."""
    import diag_common as DC
    import ff7nx_marginblack as MB
    import ff7nx_palstates as PS
    sec0 = parts[0]
    sec9 = parts[8]
    pages_l, _ts, _te, px = DC.parse_pages(sec9)
    if px != 768:
        raise ValueError('page size %d' % px)
    pm = {p.slot: p for p in pages_l if p is not None}
    effects = find_effects(sec0, name)
    if not effects:
        raise ValueError('no single-variable palette effect')
    cols, _hdr, npg, cpp = MB.palette_colours(parts[3])
    pal = cols.astype(np.int64).reshape(npg, cpp)
    used = used_params(sec0, sec9)
    free = [p for p in range(1, 64) if p not in used]
    new0 = bytearray(sec0)
    specs, notes, refused = [], [], []
    inactive = set()              # BUILD 717: palettes framed from AA REMOVED
    vars_done = set()
    tmp_used = set()
    # BUILD 714: palettes driven by one variable (nvmkin32's palettes 11
    # and 8 both read var5[15]) convert together under the same parameters,
    # or not at all -- renumbering the variable for one would break the
    # other's still-paletted op
    group = collections.defaultdict(list)
    for ef in effects:
        group[ef['var']].append(ef)
    exclude, variants = opts
    variants = dict(variants)
    done = {}
    for ef in effects:
        q = ef['q']
        if q in exclude:
            refused.append('palette %d: its rewrite does not keep the '
                           'script\'s tick timing (BUILD 858)' % q)
            continue
        try:
            gate = None
            try:
                rp, rbits = always_on(sec0, sec9, q)
            except ValueError as exc_:
                if not gates or not str(exc_).endswith(GATE_WHY):
                    raise
                gate = gate_of(sec0, sec9, q)
                rp, rbits = gate[0], gate[1]
            tg = PS._targets(sec9, pm, q, rp, rbits)
            vc = PS._vanilla_cells(tg, vparts[8])
            uses = var_uses(sec0, ef['var'],
                            skip={o_ for e_ in group[ef['var']]
                                  for o_, _l in e_['sites']})
            dead = dead_init_sets(sec0, uses, ef['sites'])
            step, anchor, lo, hi, sets = lattice(sec0, uses, dead)
            signed = False
            if (hi - lo) // step + 1 > MAX_MULTI and \
                    ef['op'] in (0xE9, 0xEF):
                # BUILD 716: a signed brightness that wraps through 0
                try:
                    step, anchor, lo, hi, sets = lattice(sec0, uses, dead,
                                                         True)
                    signed = True
                except ValueError:
                    pass
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
            states = [x & 255 for x in range(lo, hi + 1, step)]
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
            key = [(v - 256 if (ef['op'] in (0xE9, 0xEF) and v >= 128) else v)
                   for v in states]
            s_all = [states[i] for i in np.argsort(key, kind='stable')]
            artmap = {}
            for vpage, cells in byp.items():
                recs = _frames(art, name, vpage, q, inactive)
                xy = sorted({c for c, _a in cells})
                if dead and len(recs) == n + 1:
                    # BUILD 713: Cosmos also captured the init value the
                    # script never loads after its first frame (juninn's
                    # palette 9: 0 at init, 32/53 after) -- drop that frame,
                    # the dimmest when the init value is below the states
                    dv = [(_sv if signed else int)(sec0[o + 3])
                          for o in dead]
                    lum = [float(PS._cells_of(art.provider, r, xy, px).mean())
                           for r in recs]
                    if all(v < lo for v in dv):
                        recs = [r for i, r in enumerate(recs)
                                if i != int(np.argmin(lum))]
                    elif all(v > hi for v in dv):
                        recs = [r for i, r in enumerate(recs)
                                if i != int(np.argmax(lum))]
                if not dead and len(recs) == n + 1 and \
                        ef['op'] in (0xE9, 0xEF) and \
                        (max(key) < 0 or min(key) > 0):
                    # BUILD 714: Cosmos also captured the palette as the
                    # field loads it, before the first add (nvmkin32: v
                    # runs -19..-1, the load is v = 0) -- the brightest
                    # frame when every state darkens, the dimmest when
                    # every state brightens; the alignment proof below
                    # still has to pass
                    lum = [float(PS._cells_of(art.provider, r, xy, px).mean())
                           for r in recs]
                    drop = int(np.argmax(lum) if max(key) < 0
                               else np.argmin(lum))
                    recs = [r for i, r in enumerate(recs) if i != drop]
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
            done[q] = {'rp': rp, 'rbits': rbits, 'uses': uses, 'gate': gate,
                       'dead': dead, 'lat': (lo, step, n),
                       'signed': signed, 'art': artmap,
                       'ordered': s_all == states,
                       'additive': all(r[30] == 1 for _l, _i, r in tg)}
        except Exception as exc:                               # noqa: BLE001
            refused.append('palette %d: %s' % (q, str(exc)[:70]))
    edits = []
    proofs = []                   # BUILD 858: what tick_proof checks
    stream0 = _stream(sec0)
    gate_extra = {}               # BUILD 716: BG op offset -> [size, bytes]
    # BUILD 714 PAIR mode: every cell of every multi-state effect is fitted
    # both ways; the `k4` cells whose 7-image fit loses least take it
    cellfit = {}
    if pair:
        cand = []
        for q, d in done.items():
            if d['lat'][2] <= MAX_STATES:
                continue
            if not (d['ordered'] and d['additive']):
                continue
            loose = []
            for c, fr in d['art'].items():
                f2 = pair_fit(fr, 2)
                f4 = pair_fit(fr, 4) if k4 else None
                cellfit[(q, c)] = [2, f2, f4, fr]
                if f2[3] > PAIR_MAX_ERR:
                    loose.append((f2[3], c))
                elif f4 is not None:
                    cand.append((f4[3], q, c))
            # BUILD 854: a tile whose states the pair model cannot follow
            # (onna_4's lamp core: Cosmos's frames change SHAPE, a texel
            # running 10..149..92) keeps every state as its own image, as
            # one-copy-per-state would -- as long as only a few do
            if len(loose) <= PAIR_EXACT_MAX:
                for _e, c in loose:
                    cellfit[(q, c)][0] = 'x'
        for _e, q, c in sorted(cand)[:k4]:
            cellfit[(q, c)][0] = 4
    for var, efs in group.items():
        if len(efs) > 1 and any(done[e_['q']]['gate'] for e_ in efs
                                if e_['q'] in done):
            for e_ in efs:
                if done.pop(e_['q'], None) is not None:
                    refused.append('palette %d: a script-switched layer '
                                   'shares var5[%d]' % (e_['q'], var))
            continue
        if len(efs) > 1:
            bad = [e_['q'] for e_ in efs if e_['q'] not in done]
            lats = {done[e_['q']]['lat'] for e_ in efs if e_['q'] in done}
            if bad or len(lats) != 1:
                for e_ in efs:
                    if done.pop(e_['q'], None) is not None:
                        refused.append('palette %d: shares var5[%d] with '
                                       'palette(s) %s, not converted'
                                       % (e_['q'], var, bad or 'of '
                                          'another lattice'))
                continue
        lead = None
        fits = {}
        if pair and any(done[e_['q']]['lat'][2] > MAX_STATES
                        for e_ in efs if e_['q'] in done):
            # the whole group passes the fit before any of it is rewritten
            why = None
            for e_ in efs:
                q_ = e_['q']
                d = done.get(q_)
                if d is None:
                    continue
                if not (d['ordered'] and d['additive']):
                    why = 'pair mode needs additive records and states in ' \
                          'brightness order'
                    break
                fit = {c: cellfit[(q_, c)] for c in d['art']}
                errs = [v[1] if v[0] == 2 else v[2] if v[0] == 4
                        else (None, None, 0.0, 0.0) for v in fit.values()]
                worst = max(f[3] for f in errs)
                mean = float(np.mean([f[2] for f in errs]))
                if worst > PAIR_MAX_ERR or mean > PAIR_MAX_MEAN:
                    why = 'pair fit off by %.1f (mean %.2f)' % (worst, mean)
                    break
                fits[q_] = (fit, worst, mean)
            if why:
                for e_ in efs:
                    if done.pop(e_['q'], None) is not None:
                        refused.append('palette %d: %s' % (e_['q'], why))
                continue
        for ef in efs:
            q = ef['q']
            if q not in done:
                continue
            d = done[q]
            uses, dead, artmap = d['uses'], d['dead'], d['art']
            rp, rbits = d['rp'], d['rbits']
            lo, step, n = d['lat']
            hi = lo + step * (n - 1)
            sites = ef['sites']
            # BUILD 714: a palette sharing its variable with an earlier one
            # (follower) loads at its own moment in the tick -- nvmkin32's
            # palette 8 reads var5[15] after palette 11's routine stepped
            # it -- so it gets its own parameters and its own site, offset
            # from the lead's; the variable is renumbered once. A follower
            # that cannot be placed refuses the whole field (the lead's
            # renumbering is already in the script).
            follow = lead is not None
            if n <= MAX_STATES:
                if not free:
                    if follow:
                        raise ValueError('palette %d: no free parameter for '
                                         'a shared variable' % q)
                    refused.append('palette %d: no free background '
                                   'parameter' % q)
                    break
                P = free.pop(0)
                if not follow:
                    new0 = bytearray(renumber(bytes(new0), uses, lo, step,
                                              n, dead=dead,
                                              signed=d['signed']))
                    lead = P
                for so, sl in sites:
                    # BUILD 858: the vanilla site's opcode count, the
                    # switch last (it was bg_fill's BGCLR/BGON padding)
                    kv = len(_ops_in(stream0, so, sl))
                    edits.append((so, sl, site_single(kv, P, ef['var'])))
                proofs.append({'q': q, 'mode': 'single', 'P': P})
                gcode = gate_codes('single', ef['var'], P=P)
                specs.append({'palette': q, 'mode': 'levels', 'param': P,
                              'states': tuple(range(n)), 'art': artmap,
                              'record_param': rp, 'record_bits': rbits,
                              'static_tol': STATIC_TOL})
                how = 'param %d' % P
            elif pair:
                fit, worst, mean = fits[q]
                na = (n + 3) // 4
                block = _free_block(free, na)
                if block is None or len(free) < na + 1:
                    if follow:
                        raise ValueError('palette %d: no %d free parameters '
                                         'for a shared variable' % (q, na + 1))
                    refused.append('palette %d: no %d free parameters'
                                   % (q, na + 1))
                    break
                for p_ in block:
                    free.remove(p_)
                pb = free.pop(0)
                prev = _free_var(bytes(new0), taken=vars_done | tmp_used)
                tb = _free_var(bytes(new0),
                               taken=vars_done | tmp_used | {prev})
                tmp_used.update((prev, tb))
                if not follow:
                    new0 = bytearray(renumber(bytes(new0), uses, lo, step, n,
                                              base=4 * block[0], dead=dead,
                                              signed=d['signed']))
                    lead = block[0]
                gcode = gate_codes('pair', ef['var'], prev=prev, tb=tb,
                                   pb=pb)
                proofs.append({'q': q, 'mode': 'pair', 'block': block,
                               'pb': pb})
                for so, sl in sites:
                    edits.append((so, sl, pair_code(ef['var'], prev, tb, pb,
                                                    block[0] - lead)))
                layers = {}
                for c, (k_, f2, f4, fr) in fit.items():
                    layers[c] = (exact_records(fr, block) if k_ == 'x' else
                                 pair_records(f2 if k_ == 2 else f4, k_,
                                              block, pb))
                n4 = sum(1 for v in fit.values() if v[0] == 4)
                nx = sum(1 for v in fit.values() if v[0] == 'x')
                specs.append({'palette': q, 'mode': 'layers',
                              'art': layers, 'record_param': rp,
                              'record_bits': rbits})
                how = ('pair: params %d..%d + %d, var5[%d]/var5[%d], %d of '
                       '%d tiles 7 images, fit within %.1f (mean %.2f)'
                       % (block[0], block[-1], pb, prev, tb, n4, len(fit),
                          worst, mean))
                if nx:
                    how += ', %d tile(s) one image per state' % nx
            else:
                block = _free_block(free, n)
                if block is None:
                    if follow:
                        raise ValueError('palette %d: no %d consecutive free '
                                         'parameters for a shared variable'
                                         % (q, n))
                    refused.append('palette %d: no %d consecutive free '
                                   'parameters' % (q, n))
                    break
                filler = [p for p in free if p not in block]
                prev = _free_var(bytes(new0), taken=vars_done | tmp_used)
                for p in block:
                    free.remove(p)
                if not follow:
                    new0 = bytearray(renumber(bytes(new0), uses, lo, step, n,
                                              base=block[0], dead=dead,
                                              signed=d['signed']))
                    lead = block[0]
                    for so, sl in sites:
                        # BUILD 858: the vanilla site's opcode count
                        kv = len(_ops_in(stream0, so, sl))
                        edits.extend(_multi_variant(
                            stream0, sec0, so, sl, kv, ef['var'], prev,
                            variants.get(q, 0)))
                else:
                    for so, sl in sites:
                        new0[so:so + sl] = multi_follow(sl, ef['var'], prev,
                                                        block[0] - lead)
                tmp_used.add(prev)
                proofs.append({'q': q, 'mode': 'multi', 'block': block})
                gcode = gate_codes('multi', ef['var'], prev=prev)
                specs.append({'palette': q, 'mode': 'levels',
                              'params': tuple(block),
                              'states': tuple(range(n)), 'art': artmap,
                              'record_param': rp, 'record_bits': rbits,
                              'static_tol': STATIC_TOL})
                how = 'params %d..%d, var5[%d] remembers the shown one' % (
                    block[0], block[-1], prev)
            if proofs and 'var' not in proofs[-1]:
                proofs[-1].update({
                    'var': ef['var'], 'sites': sites, 'gate': d['gate'],
                    'states': [x & 255 for x in range(lo, hi + 1, step)]})
            if d['gate']:
                gp, _gm, gops = d['gate']
                vv = _free_var(bytes(new0), taken=vars_done | tmp_used)
                tmp_used.add(vv)
                show, hide = gcode
                for so, sl in sites:
                    pe = [e_ for e_ in edits if e_[0] == so]
                    if pe:
                        edits.remove(pe[0])
                        edits.append((so, sl, pe[0][2] + gate_site(vv, hide)))
                    else:
                        edits.append((so, sl, bytes(new0[so:so + sl])
                                      + gate_site(vv, hide)))
                for o_, z_, kind in gops:
                    extra = (bytes((0x80, 0x50, vv, 1)) + show
                             if kind == 'on' else
                             bytes((0x80, 0x50, vv, 0)) + hide)
                    gate_extra.setdefault(o_, [z_, b''])[1] += extra
                how += ', shown while param %d is on (var5[%d])' % (gp, vv)
            vars_done.add(ef['var'])
            notes.append('palette %d: var5[%d] %d..%d step %d -> %d states, '
                         '%s%s' % (q, ef['var'], lo, hi, step, n, how,
                                   ', frames from a folder the mod does not '
                                   'install' if q in inactive else ''))
    if not specs:
        raise ValueError('; '.join(refused) or 'nothing')
    if gate_extra and refused:
        # BUILD 716: a field whose switched layer converts but another FX
        # palette stays paletted would be half truecolor (user, 10-16: "we
        # also dont want partially applied") -- the new path refuses it whole
        raise ValueError(PARTIAL + ': ' + '; '.join(refused))
    for o_, (z_, extra) in gate_extra.items():
        edits.append((o_, z_, bytes(new0[o_:o_ + z_]) + extra))
    if edits:
        new0 = bytearray(grow(bytes(new0), edits))
    proved = []
    for pr in proofs:
        if pr['gate']:
            proved.append('palette %d: switched layer, not modelled'
                          % pr['q'])
            continue
        loose = pr['mode'] == 'multi' and \
            variants.get(pr['q'], 0) >= MULTI_VARIANTS - 1
        why = tick_proof(sec0, bytes(new0), edits, pr, relaxed=loose)
        if why is None and loose:
            proved.append('palette %d: every state in order, a tick slower '
                          'per pass than vanilla' % pr['q'])
        elif why is None:
            proved.append('palette %d: every tick matches vanilla'
                          % pr['q'])
        elif why.startswith('unverified'):
            proved.append('palette %d: %s' % (pr['q'], why))
        else:
            raise ValueError('%s: palette %d (%s): %s'
                             % (TICK_WHY, pr['q'], pr['mode'], why))
    sec0n, sec9n, info = PS.plan_field(name, parts, vparts, art, mb_cap,
                                       raw_cap, specs=specs,
                                       sec0=bytes(new0))
    info['auto'] = notes
    info['auto_refused'] = refused
    info['ticks'] = proved
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
        _fxcomplete_after(name, parts, archive, entry, art, mb_cap, raw_cap,
                          info)
        payloads[name] = encode(lgp.join_sections(parts))
        kept = [r for r in info.get('auto_refused', ())
                if 'tick timing' in r]                  # BUILD 858
        total['names'].append('%s (%s; %.1f MB)' % (
            name, ', '.join(list(info['auto']) + kept), info['mb']))
    return total


def _fxcomplete_after(name, parts, archive, entry, art, mb_cap, raw_cap,
                      info):
    """fxcomplete runs before this pass, so a field whose animated palette
    blocked it there (spipe_1 page 15: palette 4 is script-written) kept its
    static FX palettes on that page at 256 px. Once the animation has states
    here, give fxcomplete its turn again on this field. Its own skip lists
    and switch apply; any failure keeps the palauto result unchanged."""
    try:
        import lgp
        import ff7nx_fxcomplete as FC
        import ff7nx_fxpages as FP
        import ff7nx_fxpcstatic as FPS
        import ff7nx_palanim as PA
        low = name.lower()
        if FC.disabled() or low in (set(FC.EXCLUDE) | set(FC.CONFIRMED)
                                    | set(FC.HOLD) | set(FPS.FIELDS)
                                    | {n.lower() for n in FP.DEFER_FIELDS}):
            return
        import diag_common as DC
        if not any(g is not None and g.depth == 1 and
                   FC.FX_LO <= g.slot < FC.FX_HI
                   for g in DC.parse_pages(parts[8])[0]):
            return
        scripted = PA.animated_palettes(parts[0])
        if len(scripted) >= 256:
            return
        other = len(lgp.join_sections(parts)) - len(parts[8])
        s9, done, _refused, mb = FC.plan_field(
            name, parts[8], art, scripted,
            FC.MB_OVERRIDE.get(low, mb_cap), other, raw_cap,
            FC.rotates(parts[0]), parts[3], FC.set_once(parts[0]),
            FC.reference_section9(archive, entry, name))
    except Exception:                                          # noqa: BLE001
        return
    if done:
        parts[8] = s9
        info['auto'].append('static FX page(s) %s' % ','.join(
            map(str, sorted(done))))
        info['mb'] = mb


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
