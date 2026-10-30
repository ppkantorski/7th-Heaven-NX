"""BUILD 858: a tick model of one field actor, to prove a palette rewrite
keeps the script's timing.

The field interpreter gives each actor at most TICK_CAP (8) opcodes a tick;
WAIT and a backward jump (BACK / LBACK) end the tick early. Measured on
hardware twice: BUILD 619b (las2 ripples, `_scratch/c7/ticksim.py` matched
the capture exactly) and BUILD 858 (onna_4, below).

onna_4 (user, 10-28: "lights that are rapidly flickering" after BUILD 854).
lg23+ steps var5[6], then in the SAME tick runs palette 14's switch. The
vanilla tick is IF IF MINUS SKIP SKIP ADPAL LDPAL WAIT = 8 opcodes. BUILD
854's pair switch is 8 opcodes on its own, so the tick ran out after its
BGCLR: for one tick palette 14's layer was off (the six lamp sparkles went
black for one 60 Hz frame), and every pass took one tick longer (14 ticks,
measured on the capture, against vanilla's 13).

`run` executes an actor's script 0 (init up to its first RET, then the main
part) for a number of ticks and reports, per tick, the instructions it ran.
Variables are modelled for the opcodes palette loops use (SET, PLUS, MINUS,
INC, DEC, MUL, DIV, MOD and the IF family); anything that branches in a way
the model does not know makes `run` return None (unverifiable) rather than
guess.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import struct

TICK_CAP = 8

# jump opcodes the model follows: (operand width, backward)
_SKIP = {0x10: 1, 0x11: 2}
_BACK = {0x12: 1, 0x13: 2}
# IF family: opcode -> (operand width, jump width)
_IF = {0x14: (1, 1), 0x15: (1, 2), 0x16: (2, 1), 0x17: (2, 2),
       0x18: (2, 1), 0x19: (2, 2)}
# branches the model cannot follow
_OPAQUE = {0x30, 0x31, 0x32, 0xCB, 0xCC, 0x02, 0x03, 0x04, 0x05, 0x06,
           0x07, 0x01, 0x08, 0x09}
RET, WAIT, NOP = 0x00, 0x24, 0x5F


class Unverifiable(Exception):
    pass


def _size(sec0, o):
    from echo_s_flevel import instruction_size      # sets up PyFF7's path
    return instruction_size(sec0, o)


class Actor:
    def __init__(self, sec0, entry, end):
        self.s = sec0
        self.end = end
        self.v = {}
        self.pc = entry
        self.sleep = 0

    def get(self, bank, x, wide=False):
        if bank == 0:
            return x
        if bank in (5, 6) or bank in (1, 2, 3, 4, 11, 12, 13, 14, 15):
            if wide or bank in (2, 4, 6, 12, 14):
                return self.v.get((bank, x), 0) & 0xFFFF
            return self.v.get((bank, x), 0) & 0xFF
        raise Unverifiable('bank %d' % bank)

    def put(self, bank, x, val, wide=False):
        if bank == 0:
            raise Unverifiable('store to a literal')
        self.v[(bank, x)] = val & (0xFFFF if wide or bank in (2, 4, 6, 12, 14)
                                   else 0xFF)

    def step(self, bg):
        """One instruction; returns (offset, op, kind) with kind None,
        'wait', 'back' or 'ret'."""
        s, o = self.s, self.pc
        if not o < self.end:
            raise Unverifiable('ran off the routine')
        op = s[o]
        z = _size(s, o)
        nxt = o + z
        kind = None
        if op in _IF:
            w, jw = _IF[op]
            banks = s[o + 1]
            if w == 1:
                a, b, c = s[o + 2], s[o + 3], s[o + 4]
            else:
                a, b = struct.unpack_from('<HH', s, o + 2)
                c = s[o + 6]
            x = self.get(banks >> 4, a, w == 2)
            y = self.get(banks & 15, b, w == 2)
            if op in (0x16, 0x17):
                x, y = (x ^ 0x8000) - 0x8000, (y ^ 0x8000) - 0x8000
            table = {0: x == y, 1: x != y, 2: x > y, 3: x < y, 4: x >= y,
                     5: x <= y, 6: bool(x & y), 7: bool(x ^ y),
                     8: bool(x | y), 9: bool((1 << y) & x),
                     10: not ((1 << y) & x)}
            if c not in table:
                raise Unverifiable('IF condition %d' % c)
            if not table[c]:
                at = nxt - jw
                val = s[at] if jw == 1 else struct.unpack_from('<H', s, at)[0]
                nxt = at + val
        elif op in _SKIP:
            at = o + 1
            val = s[at] if _SKIP[op] == 1 else struct.unpack_from('<H', s, at)[0]
            nxt = at + val
        elif op in _BACK:
            val = s[o + 1] if _BACK[op] == 1 else \
                struct.unpack_from('<H', s, o + 1)[0]
            nxt = o - val
            kind = 'back'
        elif op == WAIT:
            self.sleep = max(0, struct.unpack_from('<H', s, o + 1)[0] - 1)
            kind = 'wait'
        elif op == RET:
            kind = 'ret'
        elif op in (0x80, 0x81):              # SETBYTE / SETWORD
            banks = s[o + 1]
            wide = op == 0x81
            src = (struct.unpack_from('<H', s, o + 3)[0] if wide
                   else s[o + 3])
            self.put(banks >> 4, s[o + 2], self.get(banks & 15, src, wide),
                     wide)
        elif op in (0x85, 0x86, 0x87, 0x88, 0x89, 0x8A, 0x8B, 0x8C, 0x8D,
                    0x8E, 0x76, 0x77, 0x78, 0x79):
            banks = s[o + 1]
            wide = op in (0x86, 0x88, 0x8A, 0x8C, 0x8E, 0x77, 0x79)
            src = (struct.unpack_from('<H', s, o + 3)[0] if wide
                   else s[o + 3])
            d, b = banks >> 4, banks & 15
            x, y = self.get(d, s[o + 2], wide), self.get(b, src, wide)
            top = 0xFFFF if wide else 0xFF
            if op in (0x85, 0x86):
                r = x + y
            elif op in (0x76, 0x77):
                r = min(top, x + y)
            elif op in (0x87, 0x88):
                r = x - y
            elif op in (0x78, 0x79):
                r = max(0, x - y)
            elif op in (0x89, 0x8A):
                r = x * y
            elif op in (0x8B, 0x8C):
                r = x // y if y else 0
            else:
                r = x % y if y else 0
            self.put(d, s[o + 2], r, wide)
        elif op in (0x95, 0x96, 0x97, 0x98, 0x7A, 0x7B, 0x7C, 0x7D):
            bank = s[o + 1] & 15
            wide = op in (0x96, 0x98, 0x7B, 0x7D)
            x = self.get(bank, s[o + 2], wide)
            top = 0xFFFF if wide else 0xFF
            r = {0x95: x + 1, 0x96: x + 1, 0x97: x - 1, 0x98: x - 1,
                 0x7A: min(top, x + 1), 0x7B: min(top, x + 1),
                 0x7C: max(0, x - 1), 0x7D: max(0, x - 1)}[op]
            self.put(bank, s[o + 2], r, wide)
        elif op in (0xE0, 0xE1, 0xE4):        # BGON / BGOFF / BGCLR
            banks = s[o + 1]
            if op == 0xE4:
                p = self.get(banks & 15, s[o + 2])
                bg[p] = 0
            else:
                p = self.get(banks >> 4, s[o + 2])
                b = self.get(banks & 15, s[o + 3])
                if op == 0xE0:
                    bg[p] = bg.get(p, 0) | (1 << b)
                else:
                    bg[p] = bg.get(p, 0) & ~(1 << b)
        elif op in _OPAQUE:
            raise Unverifiable('opcode %02X' % op)
        self.pc = nxt
        return o, op, kind


def routine_of(sec0, at):
    """(start, end) of the routine holding offset `at`, or None."""
    import echo_s_flevel as ES
    for a, b in ES._routine_blocks(sec0):
        if a <= at < b:
            return a, b
    return None


def run(sec0, entry, end, ticks, cap=TICK_CAP, watch=None):
    """Per tick: (instructions run [(offset, op)], {param: bits} after the
    tick, watch values) for the actor whose script 0 starts at `entry`.
    `watch` = [(bank, addr)] read at every instruction. None when the model
    cannot follow the script."""
    act = Actor(sec0, entry, end)
    bg = {}
    try:
        for _ in range(4096):                 # init, not timed
            _o, _op, kind = act.step(bg)
            if kind == 'ret':
                break
        else:
            raise Unverifiable('init does not return')
        main = act.pc
        out = []
        for _t in range(ticks):
            ran = []
            if act.sleep:
                act.sleep -= 1
            else:
                n = 0
                while True:
                    vals = (tuple(act.get(b, x) for b, x in watch)
                            if watch else ())
                    o, op, kind = act.step(bg)
                    ran.append((o, op, vals))
                    n += 1
                    if kind == 'ret':
                        act.pc = main
                        break
                    if kind in ('wait', 'back') or n >= cap:
                        break
            out.append((ran, dict(bg)))
        return out
    except Unverifiable:
        return None
    except (IndexError, KeyError, ValueError, struct.error):
        return None


def script0_entries(sec0):
    """{script 0 start offset: actor index}."""
    import field_dis as FD
    _meta, _names, entries = FD.parse(sec0)
    return {row[0]: i for i, row in enumerate(entries)}


def run_many(sec0, starts, ticks, watch=(), cap=TICK_CAP, inits=()):
    """`run` for several actors at once, in actor order every tick, sharing
    variables and background state (the field's temporaries are global).
    Per tick: ([(actor, offset, op, watch values)], {param: bits}); None
    when the model cannot follow any of them."""
    import echo_s_flevel as ES
    ends = {}
    blocks = ES._routine_blocks(sec0)
    for st in starts:
        ends[st] = next(b for a, b in blocks if a == st)
    shared = {}
    acts = []
    bg = {}
    try:
        # every other actor's init first (actor order): the field's
        # temporaries the loops compare against are often set there
        # (nvmkin32's bounds var5[0x13]/[0x14] live in another actor)
        for st in sorted(set(inits) - set(starts)):
            end = next((b for a, b in blocks if a == st), None)
            if end is None:
                continue
            other = Actor(sec0, st, end)
            other.v = shared
            try:
                for _ in range(4096):
                    if other.step({})[2] == 'ret':
                        break
            except Unverifiable:
                pass
        for st in sorted(starts):
            act = Actor(sec0, st, ends[st])
            act.v = shared
            for _ in range(4096):
                _o, _op, kind = act.step(bg)
                if kind == 'ret':
                    break
            else:
                raise Unverifiable('init does not return')
            act.main = act.pc
            acts.append(act)
        out = []
        for _t in range(ticks):
            ran = []
            for ai, act in enumerate(acts):
                if act.sleep:
                    act.sleep -= 1
                    continue
                n = 0
                while True:
                    vals = tuple(act.get(b, x) for b, x in watch)
                    o, op, kind = act.step(bg)
                    ran.append((ai, o, op, vals))
                    n += 1
                    if kind == 'ret':
                        act.pc = act.main
                        break
                    if kind in ('wait', 'back') or n >= cap:
                        break
            out.append((ran, dict(bg)))
        return out
    except Unverifiable:
        return None
    except (IndexError, KeyError, ValueError, StopIteration, struct.error):
        return None
