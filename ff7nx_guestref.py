#!/usr/bin/env python3
r"""
ff7nx_guestref.py -- find every ARM64 access to a GUEST address in a body.

WHY THIS EXISTS, AND WHY THE OBVIOUS SCAN IS WRONG
--------------------------------------------------
HANDOFF-90 §4.2 described the recompiled idiom for reading an FF7 global as

    mov  w19, #0xad4c ; movk w19, #0x9a, lsl #16     the guest address
    mov  w0, w19 ; bl #0x10FC3A0                     guest -> host
    ldrh w23, [x0]                                   <-- the site

and concluded "each of FFNx's 19 redirects is one word".  The one-word
conclusion is right.  The *search* implied by it is not, in two ways that both
silently under-count:

  1. The recompiler emits `mov wN, #imm16` for the low half, and capstone
     reports the mnemonic as `mov`, not `movz`.  A scan keyed on `movz`
     finds ZERO materialisations in every one of the seven bodies.

  2. The materialisation is HOISTED AND SHARED.  In battle_draw_quad_5BD473
     the pair at +0x7CCFB4 is the only one in the function; both of FFNx's
     x86 sites (+0xDA, +0x112) reach it through `mov w0, w19`, and the
     neighbouring field y is reached through `add w21, w19, #4` off the same
     register.  So materialisations are NOT sites, and there is no
     one-to-one correspondence to count against.

     This is what makes patching the materialisation the wrong move: it would
     move every field sharing that base, including ones FFNx leaves alone.
     The load is the only place where one field can be changed in isolation.

So the real question is not "where is the constant" but "at each load, what
guest address was in w0 when the translator was called".  That is a dataflow
question, and this module answers it by execution rather than by pattern.

THE MODEL
---------
Constant propagation to a fixpoint over the function's control-flow graph.

An earlier version of this module did a single linear walk and cleared its
whole state at every branch target, on the reasoning that a value arriving at
a join from two directions is not something a linear walk can claim to know.
That is sound but far too lossy to build a patch on: it recovered 4 of the 23
sites, and found ZERO in all three swirl bodies, which are loop-heavy.  A
scanner that silently drops two thirds of its targets is indistinguishable
from one that is looking in the wrong place.

So the join is handled properly instead: a register is known at a block entry
only if every predecessor agrees on its value, computed to a fixpoint.  This
is still conservative -- it can only lose sites, never invent them -- but it
loses only the ones that are genuinely ambiguous.  The caller still
cross-checks the surviving count against the x86, and that check is what
licenses the patch, not this module's confidence.

Transfers:

    mov  wD, #imm            wD = imm
    movk wD, #imm, lsl #s    wD |= imm << s      (only if wD already known)
    mov  wD, wS              wD = wS
    add  wD, wS, #imm        wD = wS + imm       <- the field-offset form
    sub  wD, wS, #imm        wD = wS - imm
    bl   <translate>         x0 := host(w0); w0..w18 forgotten
    bl   <anything else>     w0..w18 forgotten, x0 unknown
    <any other def of wD>    wD forgotten

Everything not in that list invalidates its destination.  A register whose
value we do not know is simply absent -- there is no "assume unchanged", which
is the failure mode FINDINGS-97 §5.1 caught in the emulator.

`x0_guest` -- "x0 currently holds the host address of guest G" -- is carried
in the same map under a reserved key, so it joins like any other value.  It
has to be: a load can sit in a different block from the `bl` that set it up.
"""
import bisect
import collections

from capstone import (Cs, CS_ARCH_ARM64, CS_MODE_ARM, CS_OP_IMM, CS_OP_REG,
                      CS_OP_MEM)
from capstone.arm64 import ARM64_OP_MEM

GUEST_TO_HOST = 0x10FC3A0        # the recompiler's address translator

LOADS = {'ldr': 4, 'ldrh': 2, 'ldrb': 1, 'ldrsh': 2, 'ldrsb': 1, 'ldrsw': 4,
         'ldp': 4}
STORES = {'str': 4, 'strh': 2, 'strb': 1, 'stp': 4}

CALLER_SAVED = {'w%d' % i for i in range(0, 19)} | {'x%d' % i for i in range(0, 19)}


class Access(object):
    """One ARM64 instruction that touches a guest address."""

    __slots__ = ('addr', 'guest', 'mnemonic', 'op_str', 'width', 'is_load',
                 'reg', 'word', 'base_at')

    def __init__(self, addr, guest, ins, width, is_load, reg, word, base_at):
        self.addr = addr
        self.guest = guest
        self.mnemonic = ins.mnemonic
        self.op_str = ins.op_str
        self.width = width
        self.is_load = is_load
        self.reg = reg
        self.word = word
        self.base_at = base_at

    def __repr__(self):
        return ('<%s +0x%X %s %s guest 0x%X w%d %s>'
                % ('ld' if self.is_load else 'st', self.addr, self.mnemonic,
                   self.op_str, self.guest, self.width,
                   'from +0x%X' % self.base_at if self.base_at else ''))


X0 = '@x0_guest'          # reserved key: guest address currently live in x0
COND = ('b.eq', 'b.ne', 'b.lt', 'b.le', 'b.gt', 'b.ge', 'b.hi', 'b.hs',
        'b.lo', 'b.ls', 'b.mi', 'b.pl', 'b.vs', 'b.vc', 'b.al', 'b.nv',
        'cbz', 'cbnz', 'tbz', 'tbnz')


def _succs(i, nxt, lo, hi):
    """Successor addresses of instruction `i`, restricted to [lo, hi)."""
    m = i.mnemonic
    if m == 'ret' or m == 'br':
        return []
    tgt = None
    for op in i.operands:
        if op.type == CS_OP_IMM:
            tgt = op.imm
    if m == 'b':
        return [tgt] if tgt is not None and lo <= tgt < hi else []
    if m in COND:
        out = [nxt] if nxt is not None and nxt < hi else []
        if tgt is not None and lo <= tgt < hi:
            out.append(tgt)
        return out
    return [nxt] if nxt is not None and nxt < hi else []


def _meet(a, b):
    """Values both states agree on.  None means 'no state yet' (bottom)."""
    if a is None:
        return dict(b)
    return {k: v for k, v in a.items() if b.get(k, object()) == v}


def _step(i, st, text, emit, stats, taps=None):
    """
    Apply one instruction to state `st` (mutated).  Emits accesses.

    `taps` collects (addr, guest) for every call to the address translator
    whose argument is a known constant.  That anchor matters because it is
    the ONE part of the idiom that survives patching: once a site's load is
    replaced by an immediate, the load is gone and a scanner keyed on loads
    can no longer find the site to show or revert it.  The translator call
    stays put in both states.
    """
    m = i.mnemonic
    ops = i.operands

    if (m in LOADS or m in STORES) and X0 in st:
        mem = next((o for o in ops if o.type == ARM64_OP_MEM), None)
        if mem is not None and i.reg_name(mem.mem.base) in ('x0', 'w0'):
            is_ld = m in LOADS
            reg = i.reg_name(ops[0].reg) if ops[0].type == CS_OP_REG else '?'
            if emit is not None:
                emit.append(Access(
                    i.address, st[X0] + mem.mem.disp, i,
                    (LOADS if is_ld else STORES)[m], is_ld, reg,
                    int.from_bytes(text[i.address:i.address + 4], 'little'),
                    st.get(X0 + '@src')))
            stats['ld' if is_ld else 'st'] += 1

    if m == 'bl' and ops and ops[0].type == CS_OP_IMM:
        g = st.get('w0')
        s = st.get('w0@src')
        for r in list(st):
            if r.split('@')[0] in CALLER_SAVED:
                del st[r]
        st.pop(X0, None)
        st.pop(X0 + '@src', None)
        if ops[0].imm == GUEST_TO_HOST and g is not None:
            st[X0] = g
            if s is not None:
                st[X0 + '@src'] = s
            stats['translate'] += 1
            if emit is not None and taps is not None:
                taps.append((i.address, g))
        return
    if m in ('blr', 'br'):
        st.clear()
        return

    if not ops or ops[0].type != CS_OP_REG:
        return
    d = i.reg_name(ops[0].reg)
    dd = d[1:]              # x19 and w19 are the same architectural register
    keys = ('w' + dd, 'x' + dd)

    def kill(*regs):
        for r in regs:
            for k in ('w' + r[1:], 'x' + r[1:]):
                st.pop(k, None)
                st.pop(k + '@src', None)

    def setv(v, src):
        for k in keys:
            st[k] = v
            if src is not None:
                st[k + '@src'] = src
        if d in ('x0', 'w0'):
            st.pop(X0, None)
            st.pop(X0 + '@src', None)

    if m in ('mov', 'movz') and len(ops) == 2 and ops[1].type == CS_OP_IMM:
        sh = ops[1].shift.value if ops[1].shift.type else 0
        setv((ops[1].imm << sh) & 0xFFFFFFFF, i.address)
    elif m == 'movk' and len(ops) == 2 and ops[1].type == CS_OP_IMM:
        if d in st:
            sh = ops[1].shift.value if ops[1].shift.type else 0
            v = (st[d] | (ops[1].imm << sh)) & 0xFFFFFFFF
            setv(v, st.get(d + '@src'))     # the materialisation began at the mov
        else:
            kill(d)
    elif m == 'mov' and len(ops) == 2 and ops[1].type == CS_OP_REG:
        s = i.reg_name(ops[1].reg)
        if s in st:
            setv(st[s], st.get(s + '@src'))
        else:
            kill(d)
    elif (m in ('add', 'sub') and len(ops) == 3
          and ops[1].type == CS_OP_REG and ops[2].type == CS_OP_IMM
          and not ops[2].shift.type):
        s = i.reg_name(ops[1].reg)
        if s in st:
            k = ops[2].imm
            setv(((st[s] + k) if m == 'add' else (st[s] - k)) & 0xFFFFFFFF,
                 st.get(s + '@src'))
        else:
            kill(d)
    else:
        kill(d)
        if m in ('ldp', 'stp') and len(ops) >= 2 and ops[1].type == CS_OP_REG:
            kill(i.reg_name(ops[1].reg))
        if d in ('x0', 'w0'):
            st.pop(X0, None)
            st.pop(X0 + '@src', None)


def scan(text, lo, hi, md=None):
    """
    Every guest access in text[lo:hi], by constant propagation to a fixpoint.

    Returns (accesses, stats).  `accesses` is in address order.
    """
    if md is None:
        md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
        md.detail = True
    ins_list = list(md.disasm(text[lo:hi], lo))
    if not ins_list:
        return [], collections.Counter()
    at = {i.address: k for k, i in enumerate(ins_list)}

    # ---- leaders and blocks ---------------------------------------------
    leaders = {ins_list[0].address}
    for k, i in enumerate(ins_list):
        nxt = ins_list[k + 1].address if k + 1 < len(ins_list) else None
        for s in _succs(i, nxt, lo, hi):
            leaders.add(s)
        if i.mnemonic in COND or i.mnemonic in ('b', 'br', 'ret'):
            if nxt is not None:
                leaders.add(nxt)
    leaders = sorted(l for l in leaders if l in at)

    blocks = {}
    for n, b in enumerate(leaders):
        end = leaders[n + 1] if n + 1 < len(leaders) else hi
        blocks[b] = [i for i in ins_list if b <= i.address < end]

    succ = {}
    for b, body in blocks.items():
        if not body:
            succ[b] = []
            continue
        last = body[-1]
        k = at[last.address]
        nxt = ins_list[k + 1].address if k + 1 < len(ins_list) else None
        succ[b] = [s for s in _succs(last, nxt, lo, hi) if s in blocks]

    # ---- fixpoint --------------------------------------------------------
    entry = {b: None for b in blocks}
    entry[leaders[0]] = {}
    work = collections.deque([leaders[0]])
    seen = collections.Counter()
    sink = collections.Counter()
    while work:
        b = work.popleft()
        seen[b] += 1
        if seen[b] > 200:                    # loop bodies converge long before
            continue
        st = dict(entry[b] or {})
        for i in blocks[b]:
            _step(i, st, text, None, sink)
        for s in succ[b]:
            merged = _meet(entry[s], st)
            if entry[s] is None or merged != entry[s]:
                entry[s] = merged
                work.append(s)

    # ---- one emitting pass with the settled entry states -----------------
    out = []
    taps = []
    stats = collections.Counter()
    stats['blocks'] = len(blocks)
    for b in leaders:
        if entry[b] is None:
            stats['unreached'] += 1
            continue
        st = dict(entry[b])
        for i in blocks[b]:
            _step(i, st, text, out, stats, taps)
    out.sort(key=lambda a: a.addr)
    taps.sort()
    stats['taps'] = taps
    return out, stats


def by_guest(accesses):
    d = collections.defaultdict(list)
    for a in accesses:
        d[a.guest].append(a)
    return d
