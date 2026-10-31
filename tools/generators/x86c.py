#!/usr/bin/env python3
"""x86c.py -- BUILD 868. A modded x86 code region as C for a PS3Tweaks blob.

A region is a set of x86 address ranges of ff7_en with a mod's HEXT bytes
applied, entered at one address. Every instruction becomes C on the port's
x86 context: the eight registers are locals, loaded from it at entry and
stored back at an exit and around each call. A call goes through the blob's
trampoline table (the translated function; its return slot pushed here and
popped by its `ret`, as the port's own calls do). A jump or fall-through out
of the ranges is an exit: the C returns the exit's index, and the blob entry
resumes the translated code there.

Flags are computed only where something reads them. Every reader's reaching
setters (inside the region) must be of one kind, else the region is refused.
Flags an exit leaves live are written to the context's flag bytes.

Anything outside the subset below is refused, never guessed.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import capstone
from capstone import x86_const as X

REGS = ('eax', 'ecx', 'edx', 'ebx', 'esp', 'ebp', 'esi', 'edi')
SUB = {}
for _r in ('a', 'c', 'd', 'b'):
    SUB['e%sx' % _r] = ('e%sx' % _r, 'd')
    SUB['%sx' % _r] = ('e%sx' % _r, 'w')
    SUB['%sl' % _r] = ('e%sx' % _r, 'l')
    SUB['%sh' % _r] = ('e%sx' % _r, 'h')
for _r in ('sp', 'bp', 'si', 'di'):
    SUB['e' + _r] = ('e' + _r, 'd')
    SUB[_r] = ('e' + _r, 'w')
MASK = {1: 0xFF, 2: 0xFFFF, 4: 0xFFFFFFFF}
SIGN = {1: 0x80, 2: 0x8000, 4: 0x80000000}
MEM = {1: 'U8', 2: 'U16', 4: 'U32'}
STYPE = {1: 's8', 2: 's16', 4: 's32'}
JCC = {'jo', 'jno', 'jb', 'jae', 'je', 'jne', 'jbe', 'ja', 'js', 'jns',
       'jl', 'jge', 'jle', 'jg'}
SETCC = {'set' + c[1:] for c in JCC}
# flag-setting instructions and how their flags are derived
SETS = {'add': 'add', 'sub': 'sub', 'cmp': 'sub', 'neg': 'sub',
        'and': 'logic', 'or': 'logic', 'xor': 'logic', 'test': 'logic',
        'inc': 'inc', 'dec': 'dec', 'shl': 'shift', 'shr': 'shift',
        'sar': 'shift', 'imul': 'mul', 'mul': 'mul', 'div': 'div'}
READS = {'o': 'O', 'no': 'O', 'b': 'C', 'ae': 'C', 'e': 'Z', 'ne': 'Z',
         'be': 'CZ', 'a': 'CZ', 's': 'S', 'ns': 'S', 'l': 'SO', 'ge': 'SO',
         'le': 'ZSO', 'g': 'ZSO'}
# context flag bytes: CF, ZF, SF, OF
FLAG_BYTES = ((0x20, 'b'), (0x23, 'e'), (0x24, 's'), (0x26, 'o'))


class Refused(Exception):
    pass


def _h(v):
    return '0x%Xu' % v


class Region(object):
    """One region. read(va, n) gives the modded image's bytes. live_flags:
    exit addresses ('ret' for a return) whose flags must be written out.
    calls: the blob's shared list of called x86 functions (trampoline
    order: an x86 va, or ('arm', ARM offset) for a native routine),
    extended as needed. native: {x86 call site: ARM offset} -- calls the
    port makes to a native routine instead (its text drawing), taking the
    same guest stack arguments and popping the return slot as the x86
    function would (a region must not read the eax it returns).
    subs: ranges of subroutines the region calls itself (a cave's helpers):
    such a call pushes its return address and jumps, and a `ret` inside
    them pops it and goes back to the instruction after that call.
    data: {x86 va: C expression} -- bytes the mod keeps in its cave as an
    option's value (read as byte operands at a fixed address only), which
    the blob carries in a data area of its own instead.
    pre: {entry index: C} run on entry before the first instruction, for an
    x86 register the port still holds in a host one there (from regs).
    never: instructions no run reaches (a call into another mod's cave
    behind a test of a stock byte); each becomes a trap."""

    def __init__(self, name, read, ranges, entries, live_flags=(),
                 calls=None, post=None, native=None, subs=(), data=None,
                 pre=None, never=(), tables=None, move=None):
        self.name, self.ranges = name, list(ranges)
        # move: {x86 va: x86 va} -- a 4-byte variable the mod keeps in
        # .text (read only on the port), kept at the other address instead:
        # every memory operand's address that falls in it goes there
        self.move = dict(move or {})
        # tables: {x86 va: (length, C `u8 *`)} -- data the mod keeps where
        # the port has no copy of it (over x86 code), read from the blob's
        # copy instead; a byte outside it still reads guest memory
        self.tables = dict(tables or {})
        self.data = dict(data or {})
        self.pre = dict(pre or {})
        self.never = set(never)
        self.subs = list(subs)
        self.native = dict(native or {})
        self.entries = list(entries)
        self.post = dict(post or {})
        self.live_flags = set(live_flags)
        self.calls = calls if calls is not None else []
        self.insns = {}
        md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        md.detail = True
        for lo, hi in self.ranges:
            end = lo
            for i in md.disasm(read(lo, hi - lo), lo):
                if i.address >= hi:
                    break
                self.insns[i.address] = i
                end = i.address + i.size
            if end != hi:
                raise Refused('%s: range %X-%X is not instruction aligned '
                              '(ends %X)' % (name, lo, hi, end))
        for e in self.entries:
            if e not in self.insns:
                raise Refused('%s: entry %X not in its ranges' % (name, e))
        self.returns = sorted(a + i.size for a, i in self.insns.items()
                              if self.internal(i))
        for r in self.returns:
            if r not in self.insns:
                raise Refused('%s: return %X not in its ranges' % (name, r))
        self.preds = {}
        for a, i in self.insns.items():
            for s in self.succ(i):
                self.preds.setdefault(s, []).append(a)

    # ------------------------------------------------------------ control
    def target(self, i):
        op = i.operands[0]
        if op.type != X.X86_OP_IMM:
            raise Refused('%s: indirect %s at %X' % (self.name, i.mnemonic,
                                                     i.address))
        return op.imm & 0xFFFFFFFF

    def in_sub(self, va):
        return any(lo <= va < hi for lo, hi in self.subs)

    def internal(self, i):
        """a call to one of the region's own subroutines"""
        if i.mnemonic != 'call':
            return False
        t = self.target(i)
        if not self.in_sub(t):
            return False
        if t not in self.insns:
            raise Refused('%s: call %X into a sub outside the ranges'
                          % (self.name, i.address))
        return True

    def succ(self, i):
        m, nxt = i.mnemonic, i.address + i.size
        if m == 'ret':
            if self.in_sub(i.address):
                if i.operands:
                    raise Refused('%s: ret n in a sub at %X' % (self.name,
                                                                 i.address))
                return list(self.returns)
            return ['ret']
        if m == 'call' and self.internal(i):
            return [self.target(i)]
        if m == 'jmp':
            return [self.target(i)]
        if m in JCC:
            return [self.target(i), nxt]
        return [nxt]

    # -------------------------------------------------------------- flags
    def use_def(self, i):
        m = i.mnemonic
        if m in JCC:
            return set(READS[m[1:]]), set()
        if m in SETCC:
            return set(READS[m[3:]]), set()
        if m in ('adc', 'sbb', 'pushfd', 'lahf', 'cmc', 'stc', 'clc'):
            raise Refused('%s: %s at %X' % (self.name, m, i.address))
        if m in ('inc', 'dec'):
            return set(), set('ZSO')
        if m in SETS:
            return set(), set('CZSO')
        return set(), set()

    def liveness(self):
        live_in = {a: set() for a in self.insns}

        def out_of(i):
            out = set()
            for s in self.succ(i):
                if s in self.insns:
                    out |= live_in[s]
                elif s in self.live_flags:
                    out |= set('CZSO')
            return out
        changed = True
        while changed:
            changed = False
            for a in sorted(self.insns, reverse=True):
                i = self.insns[a]
                use, defs = self.use_def(i)
                new = (out_of(i) - defs) | use
                if new != live_in[a]:
                    live_in[a], changed = new, True
        return {a: out_of(i) for a, i in self.insns.items()}

    def setters(self, at, flags):
        """the instructions whose flags reach the start of `at`; refused if
        any path from the region's entry reaches it without one"""
        out, seen, todo = set(), set(), [at]
        while todo:
            x = todo.pop()
            if x in self.entries:
                raise Refused('%s: flags at %X come from outside'
                              % (self.name, at))
            for p in self.preds.get(x, []):
                if p in seen:
                    continue
                seen.add(p)
                _use, defs = self.use_def(self.insns[p])
                if defs >= set(flags):
                    out.add(p)
                elif defs & set(flags):
                    raise Refused('%s: flags at %X partly from %X'
                                  % (self.name, at, p))
                else:
                    todo.append(p)
        return out

    def kind_at(self, at, flags):
        kinds = {self.kinds.get(p) for p in self.setters(at, flags)}
        if len(kinds) != 1 or None in kinds:
            raise Refused('%s: flags at %X from mixed setters %s'
                          % (self.name, at, kinds))
        return kinds.pop()

    def flag_expr(self, kind, size, cc, where):
        m, s = _h(MASK[size]), _h(SIGN[size])
        Z = '((fr & %s) == 0)' % m
        S = '((fr & %s) != 0)' % s
        C = O = None
        if kind == 'sub':
            C = '((fa & %s) < (fb & %s))' % (m, m)
            O = '((((fa ^ fb) & (fa ^ fr)) & %s) != 0)' % s
        elif kind == 'add':
            C = '((fr & %s) < (fa & %s))' % (m, m)
            O = '(((~(fa ^ fb) & (fa ^ fr)) & %s) != 0)' % s
        elif kind == 'logic':
            C = O = '0'
        elif kind == 'inc':
            O = '((fr & %s) == %s)' % (m, s)
        elif kind == 'dec':
            O = '((fa & %s) == %s)' % (m, s)
        else:
            raise Refused('%s: %s flags read at %X' % (self.name, kind,
                                                       where))
        if C is None and 'C' in READS[cc]:
            raise Refused('%s: CF after inc/dec read at %X' % (self.name,
                                                                where))
        return {'o': O, 'no': '!' + str(O), 'b': C, 'ae': '!' + str(C),
                'e': Z, 'ne': '!' + Z, 'be': '(%s || %s)' % (C, Z),
                'a': '(!%s && !%s)' % (C, Z), 's': S, 'ns': '!' + S,
                'l': '(%s != %s)' % (S, O), 'ge': '(%s == %s)' % (S, O),
                'le': '(%s || %s != %s)' % (Z, S, O),
                'g': '(!%s && %s == %s)' % (Z, S, O)}[cc]

    def cond(self, i, cc):
        if cc in ('e', 'ne'):
            # ZF alone: fr is kept masked to its setter's size, so setters
            # of different sizes (a `cmp word` and a `cmp cl` both reaching
            # one `je`) agree on it
            kinds = {self.kinds.get(p) for p in self.setters(i.address,
                                                             READS[cc])}
            if None not in kinds and len(kinds) > 1:
                return '(fr == 0)' if cc == 'e' else '(fr != 0)'
        kind, size = self.kind_at(i.address, READS[cc])
        return self.flag_expr(kind, size, cc, i.address)

    def setflags(self, i, kind, size, a, b, r):
        self.kinds[i.address] = (kind, size)
        if not self.live[i.address]:
            return ''
        if size < 4:
            r = '((%s) & %s)' % (r, _h(MASK[size]))
        return 'fa = %s; fb = %s; fr = %s; ' % (a, b, r)

    # ----------------------------------------------------------- operands
    def reg(self, i, rid):
        n = i.reg_name(rid)
        if n not in SUB:
            raise Refused('%s: register %s at %X' % (self.name, n,
                                                     i.address))
        return SUB[n]

    def rd_reg(self, i, rid):
        base, k = self.reg(i, rid)
        return {'d': base, 'w': '(%s & 0xFFFFu)' % base,
                'l': '(%s & 0xFFu)' % base,
                'h': '((%s >> 8) & 0xFFu)' % base}[k]

    def wr_reg(self, i, rid, v):
        base, k = self.reg(i, rid)
        self.modified.add(base)
        return {'d': '%s = %s;' % (base, v),
                'w': '%s = (%s & 0xFFFF0000u) | ((%s) & 0xFFFFu);'
                     % (base, base, v),
                'l': '%s = (%s & ~0xFFu) | ((%s) & 0xFFu);' % (base, base, v),
                'h': '%s = (%s & ~0xFF00u) | (((%s) & 0xFFu) << 8);'
                     % (base, base, v)}[k]

    def addr(self, i, op):
        mm = op.mem
        if mm.segment != 0:
            raise Refused('%s: segment at %X' % (self.name, i.address))
        parts = []
        if mm.base:
            parts.append(self.rd_reg(i, mm.base))
        if mm.index:
            parts.append('%s * %du' % (self.rd_reg(i, mm.index), mm.scale))
        if mm.disp or not parts:
            parts.append(_h(mm.disp & 0xFFFFFFFF))
        e = '(u32)(%s)' % ' + '.join(parts)
        for old, new in self.move.items():
            e = '((u32)(%s - %s) < 4u ? (u32)(%s - %s + %s) : %s)' % (
                e, _h(old), e, _h(old), _h(new), e)
        return e

    def _bytes(self, i, lo, n):
        """the C value of the n data bytes at lo (little endian), None when
        none of them is data"""
        hit = [lo + k in self.data for k in range(n)]
        if not any(hit):
            return None
        if not all(hit):
            raise Refused('%s: access at %X covers data in part'
                          % (self.name, i.address))
        return '(u32)(%s)' % ' | '.join(
            '((u32)(%s) << %d)' % (self.data[lo + k], 8 * k) if k else
            '(u32)(%s)' % self.data[lo] for k in range(n))

    def datum(self, i, op):
        """the data a fixed-address memory operand reads, None when it
        touches no data byte"""
        mm = op.mem
        if mm.base or mm.index:
            return None
        return self._bytes(i, mm.disp & 0xFFFFFFFF, op.size)

    def imm(self, i, op, size):
        """an immediate operand, from the data area when its encoding
        overlaps data (an option's value kept in an instruction)"""
        v = self._bytes(i, i.address + i.imm_offset, i.imm_size) \
            if self.data else None
        if v is None:
            return _h(op.imm & MASK[size])
        if i.imm_size < size:
            v = '((u32)(s32)(%s)%s)' % (STYPE[i.imm_size], v)
        return '(%s & %s)' % (v, _h(MASK[size]))

    def table(self, i, op):
        """(lo, length, base) of the table a memory operand's displacement
        points into, or None"""
        disp = op.mem.disp & 0xFFFFFFFF
        for lo, (n, base) in self.tables.items():
            if lo <= disp < lo + n:
                return lo, n, base
        return None

    def rd(self, i, op, size=None):
        if op.type == X.X86_OP_REG:
            return self.rd_reg(i, op.reg)
        if op.type == X.X86_OP_IMM:
            return self.imm(i, op, size or op.size)
        t = self.table(i, op)
        if t is not None:
            lo, n, base = t
            a = self.addr(i, op)
            parts = []
            for k in range(op.size):
                x = '(%s + %du)' % (a, k)
                b = ('((u32)(%s - %s) < %du ? (u32)((const u8 *)(%s))'
                     '[%s - %s] : (u32)U8(%s))' % (x, _h(lo), n, base, x,
                                                   _h(lo), x))
                parts.append(b if not k else '(%s << %d)' % (b, 8 * k))
            return '(u32)(%s)' % ' | '.join(parts)
        d = self.datum(i, op)
        if d is not None:
            return d
        return '%s(%s)' % (MEM[op.size], self.addr(i, op))

    def wr(self, i, op, v):
        if op.type == X.X86_OP_REG:
            return self.wr_reg(i, op.reg, v)
        if self.datum(i, op) is not None or self.table(i, op) is not None:
            raise Refused('%s: write to data at %X' % (self.name, i.address))
        return '%s(%s) = %s;' % (MEM[op.size], self.addr(i, op), v)

    # -------------------------------------------------------- instruction
    def insn(self, i):
        m, ops = i.mnemonic, i.operands
        if i.address in self.never:
            return '__builtin_trap();'
        imm = range(i.address + i.imm_offset,
                    i.address + i.imm_offset + i.imm_size) \
            if i.imm_size else range(0)
        for va in range(i.address, i.address + i.size):
            if va in self.data and va not in imm:
                raise Refused('%s: data in the encoding of %X'
                              % (self.name, i.address))
        sz = ops[0].size if ops else 4
        if m == 'nop':
            return ''
        if m == 'mov':
            return self.wr(i, ops[0], self.rd(i, ops[1], sz))
        if m in ('movzx', 'movsx'):
            v = self.rd(i, ops[1])
            if m == 'movsx':
                v = '((u32)(s32)(%s)%s & %s)' % (STYPE[ops[1].size], v,
                                                 _h(MASK[sz]))
            return self.wr(i, ops[0], v)
        if m == 'lea':
            return self.wr(i, ops[0], self.addr(i, ops[1]))
        if m in ('add', 'sub', 'and', 'or', 'xor', 'cmp', 'test'):
            a, b = self.rd(i, ops[0]), self.rd(i, ops[1], sz)
            if m == 'xor' and ops[1].type == X.X86_OP_REG and \
                    ops[0].type == X.X86_OP_REG and ops[0].reg == ops[1].reg:
                a = b = '0u'
            op = {'add': '+', 'sub': '-', 'cmp': '-', 'and': '&',
                  'test': '&', 'or': '|', 'xor': '^'}[m]
            out = 't = %s; u = %s; r = (t %s u) & %s; ' % (a, b, op,
                                                           _h(MASK[sz]))
            out += self.setflags(i, SETS[m], sz, 't', 'u', 'r')
            if m not in ('cmp', 'test'):
                out += self.wr(i, ops[0], 'r')
            return out
        if m == 'not':
            return self.wr(i, ops[0], '(~%s) & %s' % (self.rd(i, ops[0]),
                                                      _h(MASK[sz])))
        if m == 'neg':
            out = 't = %s; r = (0u - t) & %s; ' % (self.rd(i, ops[0]),
                                                   _h(MASK[sz]))
            out += self.setflags(i, 'sub', sz, '0u', 't', 'r')
            return out + self.wr(i, ops[0], 'r')
        if m in ('inc', 'dec'):
            out = 't = %s; r = (t %s 1u) & %s; ' % (
                self.rd(i, ops[0]), '+' if m == 'inc' else '-', _h(MASK[sz]))
            out += self.setflags(i, m, sz, 't', '1u', 'r')
            return out + self.wr(i, ops[0], 'r')
        if m in ('shl', 'shr', 'sar'):
            self.kinds[i.address] = ('shift', sz)
            if self.live[i.address]:
                raise Refused('%s: shift flags read after %X' % (self.name,
                                                                 i.address))
            a = self.rd(i, ops[0])
            if len(ops) == 1:
                c = '1u'
            elif ops[1].type == X.X86_OP_IMM:
                c = '%du' % (ops[1].imm & 31)
            else:
                c = '(%s & 31u)' % self.rd(i, ops[1])
            if m == 'shl':
                v = '((%s << %s) & %s)' % (a, c, _h(MASK[sz]))
            elif m == 'shr':
                v = '(%s >> %s)' % (a, c)
            else:
                v = '((u32)((%s)%s >> %s) & %s)' % (STYPE[sz], a, c,
                                                    _h(MASK[sz]))
            return self.wr(i, ops[0], v)
        if m == 'imul' and len(ops) == 1:
            self.kinds[i.address] = ('mul', sz)
            if sz != 4 or self.live[i.address]:
                raise Refused('%s: imul at %X' % (self.name, i.address))
            self.modified.update(('eax', 'edx'))
            return ('{ long long p = (long long)(s32)eax * (s32)%s; '
                    'eax = (u32)p; edx = (u32)((u64)p >> 32); }'
                    % self.rd(i, ops[0]))
        if m == 'mul':
            self.kinds[i.address] = ('mul', sz)
            if sz not in (1, 4) or self.live[i.address]:
                raise Refused('%s: mul at %X' % (self.name, i.address))
            self.modified.update(('eax', 'edx') if sz == 4 else ('eax',))
            if sz == 1:
                return ('eax = (eax & 0xFFFF0000u) | (((eax & 0xFFu) * %s) '
                        '& 0xFFFFu);' % self.rd(i, ops[0]))
            return ('{ u64 p = (u64)eax * %s; eax = (u32)p; '
                    'edx = (u32)(p >> 32); }' % self.rd(i, ops[0]))
        if m == 'div' and sz == 2:
            self.kinds[i.address] = ('div', sz)
            if self.live[i.address]:
                raise Refused('%s: div at %X' % (self.name, i.address))
            self.modified.update(('eax', 'edx'))
            # as the 32-bit one: a zero divisor or a quotient past 16 bits
            # (an x86 fault) gives 0 / the low 16 bits
            return ('{ u32 n = ((edx & 0xFFFFu) << 16) | (eax & 0xFFFFu); '
                    'u32 d = %s; u32 q = d ? n / d : 0u; '
                    'u32 m = d ? n %% d : 0u; '
                    'eax = (eax & 0xFFFF0000u) | (q & 0xFFFFu); '
                    'edx = (edx & 0xFFFF0000u) | (m & 0xFFFFu); }'
                    % self.rd(i, ops[0]))
        if m == 'div':
            self.kinds[i.address] = ('div', sz)
            if sz != 4 or self.live[i.address]:
                raise Refused('%s: div at %X' % (self.name, i.address))
            self.modified.update(('eax', 'edx'))
            # x86 faults on a zero divisor or a quotient past 32 bits; the
            # blob cannot, so it gives 0 / the low 32 bits instead
            return ('{ u64 n = ((u64)edx << 32) | eax; u32 d = %s; '
                    'eax = d ? (u32)(n / d) : 0u; edx = d ? (u32)(n %% d) : '
                    '0u; }' % self.rd(i, ops[0]))
        if m == 'rep movsd':
            if i.prefix[0] != 0xF3:
                raise Refused('%s: %s at %X' % (self.name, m, i.address))
            self.modified.update(('ecx', 'esi', 'edi'))
            return ('while (ecx) { U32(edi) = U32(esi); esi += 4u; '
                    'edi += 4u; ecx--; }')
        if m == 'imul':
            self.kinds[i.address] = ('mul', sz)
            if sz != 4 or self.live[i.address]:
                raise Refused('%s: imul at %X' % (self.name, i.address))
            x, y = (ops[1], ops[2]) if len(ops) == 3 else (ops[0], ops[1])
            return self.wr(i, ops[0], '(u32)((s32)%s * (s32)%s)' % (
                self.rd(i, x), self.rd(i, y, 4)))
        if m == 'cdq':
            self.modified.add('edx')
            return 'edx = (eax & 0x80000000u) ? 0xFFFFFFFFu : 0u;'
        if m == 'push':
            self.modified.add('esp')
            v = self.rd(i, ops[0], 4)
            if ops[0].size != 4:
                raise Refused('%s: push of %d bytes at %X'
                              % (self.name, ops[0].size, i.address))
            return 't = %s; esp -= 4u; U32(esp) = t;' % v
        if m == 'pop':
            self.modified.add('esp')
            return 't = U32(esp); esp += 4u; ' + self.wr(i, ops[0], 't')
        if m in SETCC:
            return self.wr(i, ops[0], '(%s) ? 1u : 0u'
                           % self.cond(i, m[3:]))
        if m == 'call' and self.internal(i):
            self.modified.add('esp')
            return 'esp -= 4u; U32(esp) = %s; %s' % (
                _h(i.address + i.size), self.goto(self.target(i)))
        if m == 'call':
            fn = self.target(i)
            if i.address in self.native:
                fn = ('arm', self.native[i.address])
            if fn not in self.calls:
                self.calls.append(fn)
            self.modified.update(('eax', 'ecx', 'edx', 'esp'))
            return ('SYNC; ctx[4] = esp - 4u; ((fn_t)(fns + %d))(); '
                    'eax = ctx[0]; ecx = ctx[1]; edx = ctx[2]; esp = ctx[4];'
                    ' /* %s */' % (4 * self.calls.index(fn),
                                   'ARM +0x%X' % fn[1] if isinstance(fn, tuple)
                                   else 'x86 0x%X' % fn))
        if m == 'jmp':
            return self.goto(self.target(i))
        if m in JCC:
            return 'if (%s) %s' % (self.cond(i, m[1:]),
                                   self.goto(self.target(i)))
        if m == 'ret' and self.in_sub(i.address):
            self.modified.add('esp')
            for r in self.returns:
                self.targets.add(r)
            return ('t = U32(esp); esp += 4u; switch (t) { %s } '
                    '__builtin_trap();' % ' '.join(
                        'case %s: goto L%X;' % (_h(r), r)
                        for r in self.returns))
        if m == 'ret':
            self.modified.add('esp')
            n = ops[0].imm if ops else 0
            return 'esp += %du; %s' % (4 + n, self.exit_to('ret'))
        raise Refused('%s: %s %s at %X' % (self.name, m, i.op_str,
                                           i.address))

    def goto(self, va):
        if va in self.insns:
            self.targets.add(va)
            return 'goto L%X;' % va
        return self.exit_to(va)

    def exit_to(self, va):
        if va not in self.exits:
            self.exits.append(va)
        return 'goto X%d;' % self.exits.index(va)

    # --------------------------------------------------------------- text
    def gen(self):
        self.live = self.liveness()
        self.kinds, self.targets, self.modified = {}, set(), set()
        self.exits = []
        order = sorted(self.insns)
        body = {}
        # setters first: a reader needs its setters' kinds
        for a in order:
            if self.insns[a].mnemonic in SETS:
                body[a] = self.insn(self.insns[a])
        for a in order:
            if a not in body:
                body[a] = self.insn(self.insns[a])
        lines, prev = [], None
        for a in order:
            i = self.insns[a]
            if prev is not None and prev.address + prev.size != a and \
                    prev.mnemonic not in ('jmp', 'ret'):
                lines.append(('', self.goto(prev.address + prev.size)))
            lines.append((a, '/* %X %s %s */ %s' % (a, i.mnemonic, i.op_str,
                                                     body[a])))
            prev = i
        if prev.mnemonic not in ('jmp', 'ret'):
            lines.append(('', self.goto(prev.address + prev.size)))
        exit_text = []
        for k, va in enumerate(self.exits):
            flags = ''
            if va in self.live_flags:
                kinds = set()
                for a, i in self.insns.items():
                    if va in self.succ(i):
                        kinds |= ({self.kinds.get(a)} if self.use_def(i)[1]
                                  >= set('CZSO') else
                                  {self.kind_at(a, 'CZSO')})
                if len(kinds) != 1 or None in kinds:
                    raise Refused('%s: flags at exit %s from %s'
                                  % (self.name, va, kinds))
                kind, size = kinds.pop()
                flags = ' '.join('((u8 *)ctx)[0x%X] = %s;' % (
                    off, self.flag_expr(kind, size, cc, 0))
                    for off, cc in FLAG_BYTES)
            exit_text.append('X%d: /* %s */' % (
                k, va if va == 'ret' else 'x86 0x%X' % va))
            exit_text.append('    SYNC; %s%s return %du;' % (
                flags, self.post.get(va, ''), k))
        import re
        for text in self.pre.values():
            self.modified.update(re.findall(r'\b(%s)\s*=[^=]' % '|'.join(REGS),
                                            text))
        mod = [r for r in REGS if r in self.modified]
        sync = ' '.join('ctx[%d] = %s;' % (REGS.index(r), r) for r in mod)
        out = ['/* %s: x86 %s; exits %s */' % (
            self.name, ', '.join('%X-%X' % r for r in self.ranges),
            ', '.join(v if v == 'ret' else '%X' % v for v in self.exits)),
            'PT static u32 x3_%s(u32 *ctx, g2h_t g2h, char *fns, u32 entry, '
            'u64 *regs)' % self.name, '{',
            '    u32 %s;' % ', '.join('%s = ctx[%d]' % (r, k)
                                       for k, r in enumerate(REGS)),
            '    u32 t, u, r, fa = 0, fb = 0, fr = 0;',
            '    (void)t; (void)u; (void)r; (void)fa; (void)fb; (void)fr;',
            '    (void)fns; (void)regs; (void)eax; (void)ecx; (void)edx; (void)ebx;',
            '    (void)esp; (void)ebp; (void)esi; (void)edi;',
            '#define SYNC %s' % (sync or '(void)0')]
        for k in self.pre:
            if not 0 <= k < len(self.entries):
                raise Refused('%s: pre for no entry %d' % (self.name, k))
        if self.entries != [order[0]] or self.pre:
            out.append('    switch (entry) {')
            for k, e in enumerate(self.entries):
                out.append('    case %d: %s goto L%X;' % (
                    k, self.pre.get(k, ''), e))
                self.targets.add(e)
            out.append('    }')
        for a, text in lines:
            if a != '' and a in self.targets:
                out.append('L%X:' % a)
            out.append('    ' + text)
        out += exit_text
        out += ['#undef SYNC', '}']
        return '\n'.join(out) + '\n'
