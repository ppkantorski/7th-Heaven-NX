#!/usr/bin/env python3
"""ff7nx_esuicave.py -- the code caves that carry ESUI's screens beyond the main
menu (BUILD 624).

WHAT A PORT IS
ESUI patches x86 instructions the Switch never runs. For each patched function
the ARM body of the port gets, from ff7nx_esui_ports.PORTS (generated offline
by tools/esui/port, every function checked in the ARM emulator against the
x86 with ESUI applied):

  words      in-place word edits (immediates, branches);
  sites      call sites whose arguments ESUI changes: the `bl` becomes a `bl`
             to a cave that rewrites those arguments on the guest stack and
             tail-branches to the callee. The transforms (constant / +d /
             affine) were fitted from the x86 and hold for every state the
             emulator drove;
  posts      a site's argument += k when a guest byte equals v (ESUI cave
             code that rewrites x86 code bytes at run time);
  hooks      side effects of ESUI's own x86 caves (byte stores), run before
             the displaced word, every register and the flags preserved;
             ('skip_lt8', addr, v) runs the ops after it only when the guest
             byte at addr is >= v;
  replace    one word replaced by a short position-independent sequence;
  calls      BUILD 669: (site, x86 fn, constant args, expected word) -- a
             guest call made before the word at site (our own additions);
  backdrop   BUILD 669b: (site, expected word, rects, z, colour) -- opaque
             quads queued in the window-fill object before the word at site;
  skip_calls a call ESUI nops (the port's callee pops its return slot, so
             the cave pops it);
  store_const a `str wN, [x0]` whose wN is a shared constant: store k instead;
  stores     a store (push or global) whose value ESUI changes but no call
             site can carry, because the port merged several x86 sites into
             one ARM path: the store writes f(wN) instead (+d, +d on the
             low byte as x86 'add al', affine, or a value map keyed on what
             the port stores -- table constants only).

WHERE
The ARM body of x86 0x6AF19C (45 KB) that nothing reaches -- no branch, no
relocation but its own map record, its address nowhere in the exe. Proven by
ff7nx_fieldbg._liveness on the module being patched, every build. Caves are
laid out from +0x40 and every word they overwrite must still be the dump's.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

from __future__ import annotations

import struct

import a64 as A

DEAD_X86 = 0x6AF19C
DEAD_GUARD = 0x40
TRANSLATE = 0x10FC3A0
CTX_PTR = 0x12CE2B0


SDIV = lambda rd, rn, rm: 0x1AC00C00 | (rm << 16) | (rn << 5) | rd      # noqa: E731
STORE_SIZES = {0xB9000000: 4, 0x79000000: 2, 0x39000000: 1}


def _mov32(rd, v):
    v &= 0xFFFFFFFF
    out = [A.movz(rd, v & 0xFFFF)]
    if v >> 16:
        out.append(A.movk_hi(rd, v >> 16))
    return out


def _centre(w, pc, j, f, x86_to_arm):
    """arg j = (K - width(arg js)) >> 1, the width from the port's own text
    measure (a real guest call: arg pushed, return slot, the callee pops it).
    ESUI's 0x9139DD centres a title this way."""
    _, k, js, measure_x86 = f
    w.append(A.stp64_pre(19, 20, 31, -16))
    w.append(A.adrp(19, pc(), CTX_PTR))
    w.append(A.ldr64(19, 19, CTX_PTR & 0xFFF))
    w.append(A.ldr(8, 19, 0x10))
    w.append(A.add_imm(0, 8, 4 + 4 * js))
    w.append(A.bl(pc(), TRANSLATE))
    w.append(A.ldr(20, 0, 0))                                       # the string
    w.append(A.ldr(8, 19, 0x10))
    w.append(A.sub_imm(8, 8, 4))
    w.append(A.str_(8, 19, 0x10))
    w.append(A.mov_reg(0, 8))
    w.append(A.bl(pc(), TRANSLATE))
    w.append(A.str_(20, 0, 0))                                      # push it
    w.append(A.ldr(8, 19, 0x10))
    w.append(A.sub_imm(8, 8, 4))
    w.append(A.str_(8, 19, 0x10))                                   # return slot
    w.append(A.bl(pc(), x86_to_arm[measure_x86]))
    w.append(A.ldr(8, 19, 0x10))
    w.append(A.add_imm(8, 8, 4))
    w.append(A.str_(8, 19, 0x10))                                   # pop the arg
    w.append(A.ldr(20, 19, 0))                                      # eax: the width
    w += _mov32(9, k)
    w.append(A.sub_reg(20, 9, 20))
    w.append(A.lsr(20, 20, 1))
    w.append(A.add_imm(0, 8, 4 + 4 * j))
    w.append(A.bl(pc(), TRANSLATE))
    w.append(A.str_(20, 0, 0))
    w.append(A.ldp64_post(19, 20, 31, 16))


def _split_cave(at, callee_arm, parts, nargs):
    """One text call drawn as several: for each (dx, string) the callee gets
    the original args with arg0 + dx and arg2 = string. The original call is
    consumed (its return slot popped, as the callee would) and the cave
    returns to the site. ESUI's one 'Buy  Sell  Exit' string drawn as three
    words on the cursor's grid (BUILD 628)."""
    w = []
    pc = lambda: at + 4 * len(w)                                  # noqa: E731
    w.append(A.stp64_pre(29, 30, 31, -16))
    w.append(A.stp64_pre(19, 20, 31, -16))
    w.append(A.stp64_pre(21, 22, 31, -16))
    w.append(A.adrp(19, pc(), CTX_PTR))
    w.append(A.ldr64(19, 19, CTX_PTR & 0xFFF))
    w.append(A.ldr(20, 19, 0x10))                                   # esp at the site

    def push21():
        w.append(A.ldr(8, 19, 0x10))
        w.append(A.sub_imm(8, 8, 4))
        w.append(A.str_(8, 19, 0x10))
        w.append(A.mov_reg(0, 8))
        w.append(A.bl(pc(), TRANSLATE))
        w.append(A.str_(21, 0, 0))
    for dx, string in parts:
        for k in range(nargs - 1, -1, -1):
            if k == 2:
                w += _mov32(21, string)
            else:
                w.append(A.add_imm(0, 20, 4 + 4 * k))
                w.append(A.bl(pc(), TRANSLATE))
                w.append(A.ldr(21, 0, 0))
                if k == 0 and dx:
                    w += _mov32(22, dx)
                    w.append(A.add_reg(21, 21, 22))
            push21()
        w.append(A.ldr(8, 19, 0x10))
        w.append(A.sub_imm(8, 8, 4))
        w.append(A.str_(8, 19, 0x10))                               # return slot
        w.append(A.bl(pc(), callee_arm))
        w.append(A.ldr(8, 19, 0x10))
        w.append(A.add_imm(8, 8, 4 * nargs))
        w.append(A.str_(8, 19, 0x10))                               # pop its args
    w.append(A.add_imm(8, 20, 4))
    w.append(A.str_(8, 19, 0x10))                                   # the site's return slot
    w.append(A.ldp64_post(21, 22, 31, 16))
    w.append(A.ldp64_post(19, 20, 31, 16))
    w.append(A.ldp64_post(29, 30, 31, 16))
    w.append(A.ret())
    return w


def callsite_cave(at, callee_arm, tr, post=(), x86_to_arm=None):
    if -1 in tr:
        _, parts, nargs = tr[-1]
        return _split_cave(at, callee_arm, parts, nargs)
    w = []
    pc = lambda: at + 4 * len(w)                                  # noqa: E731
    w.append(A.stp64_pre(29, 30, 31, -16))
    for j, f in sorted(tr.items(), key=lambda jf: jf[1][0] == 'centre'):   # centring last
        if f[0] == 'centre':
            _centre(w, pc, j, f, x86_to_arm)
            continue
        w.append(A.adrp(0, pc(), CTX_PTR))
        w.append(A.ldr64(0, 0, CTX_PTR & 0xFFF))
        w.append(A.ldr(0, 0, 0x10))
        w.append(A.add_imm(0, 0, 4 + 4 * j))
        w.append(A.bl(pc(), TRANSLATE))
        if f[0] == 'const':
            w += _mov32(8, f[1])
        elif f[0] == 'add':
            w.append(A.ldr(8, 0, 0))
            d = f[1]
            if 0 <= d < 4096:
                w.append(A.add_imm(8, 8, d))
            elif -4096 < d < 0:
                w.append(A.sub_imm(8, 8, -d))
            else:
                w += _mov32(9, d)
                w.append(A.add_reg(8, 8, 9))
        elif f[0] == 'addb':                       # x86 'add al, k'
            w.append(A.ldr(8, 0, 0))
            w.append(A.add_imm(9, 8, f[1]))
            w.append(A.bfi(8, 9, 0, 8))
        elif f[0] == 'addmap':                     # BUILD 669: + d, then single values remapped
            _, d, pairs = f
            w.append(A.ldr(8, 0, 0))
            w += _mov32(9, d)
            w.append(A.add_reg(8, 8, 9))
            for k, v in pairs:
                w += _mov32(9, k)
                w.append(A.cmp_reg(8, 9))
                skip = len(w)
                w.append(0)
                w += _mov32(8, v)
                w[skip] = A.bcond(at + 4 * skip, at + 4 * len(w), 1)     # b.ne
        elif f[0] == 'affd':
            _, num, den, o1, n1 = f
            w.append(A.ldr(8, 0, 0))
            w += _mov32(9, o1)
            w.append(A.sub_reg(8, 8, 9))
            w += _mov32(9, num)
            w.append(A.mul(8, 8, 9))
            w += _mov32(9, den)
            w.append(SDIV(8, 8, 9))
            w += _mov32(9, n1)
            w.append(A.add_reg(8, 8, 9))
        else:
            _, num, sh, o1, n1 = f
            w.append(A.ldr(8, 0, 0))
            w += _mov32(9, o1)
            w.append(A.sub_reg(8, 8, 9))
            if num != 1:
                w += _mov32(9, num)
                w.append(A.mul(8, 8, 9))
            if sh:
                w.append(A.asr(8, 8, sh))
            w += _mov32(9, n1)
            w.append(A.add_reg(8, 8, 9))
        w.append(A.str_(8, 0, 0))
    for j, addr, val, add in post:
        w += _mov32(0, addr)
        w.append(A.bl(pc(), TRANSLATE))
        w.append(A.ldrb(9, 0, 0))
        w.append(A.cmp_imm(9, val))
        skip_at = len(w)
        w.append(0)
        w.append(A.adrp(0, pc(), CTX_PTR))
        w.append(A.ldr64(0, 0, CTX_PTR & 0xFFF))
        w.append(A.ldr(0, 0, 0x10))
        w.append(A.add_imm(0, 0, 4 + 4 * j))
        w.append(A.bl(pc(), TRANSLATE))
        w.append(A.ldr(8, 0, 0))
        w += _mov32(9, add)
        w.append(A.add_reg(8, 8, 9))
        w.append(A.str_(8, 0, 0))
        w[skip_at] = A.bcond(at + 4 * skip_at, pc(), 1)
    w.append(A.ldp64_post(29, 30, 31, 16))
    w.append(A.b(pc(), callee_arm))
    return w


def _centre_store(at, site, old, f, x86_to_arm):
    """A push `str wN, [x0]` that stores (K - width(the string `rel` slots up
    the guest stack)) >> 1 instead: ESUI's 0x9139DD centring a title. A real
    nested guest call, so every caller-saved register and the flags are kept."""
    _, k, rel, measure_x86 = f
    if (old & 0xFFFFFFE0) != 0xB9000000:
        raise ValueError('centre store +0x%X: not str wN, [x0]' % site)
    w = []
    pc = lambda: at + 4 * len(w)                                  # noqa: E731
    w.append(A.sub_imm64(31, 31, 0xb0))
    for r in range(0, 18, 2):
        w.append(A.stp64_off(r, r + 1, 31, 8 * r))
    w.append(A.stp64_off(18, 30, 31, 144))
    w.append(A.mrs_nzcv(1))
    w.append(A.str64(1, 31, 0xa0))

    def ctx():
        w.append(A.adrp(9, pc(), CTX_PTR))
        w.append(A.ldr64(9, 9, CTX_PTR & 0xFFF))
    ctx()
    w.append(A.ldr(8, 9, 0x10))
    w.append(A.add_imm(0, 8, 4 * rel))
    w.append(A.bl(pc(), TRANSLATE))
    w.append(A.ldr(10, 0, 0))
    w.append(A.str_(10, 31, 0xa8))                                  # the string
    ctx()
    w.append(A.ldr(8, 9, 0x10))
    w.append(A.sub_imm(8, 8, 4))
    w.append(A.str_(8, 9, 0x10))
    w.append(A.mov_reg(0, 8))
    w.append(A.bl(pc(), TRANSLATE))
    w.append(A.ldr(10, 31, 0xa8))
    w.append(A.str_(10, 0, 0))                                      # push it
    ctx()
    w.append(A.ldr(8, 9, 0x10))
    w.append(A.sub_imm(8, 8, 4))
    w.append(A.str_(8, 9, 0x10))                                    # return slot
    w.append(A.bl(pc(), x86_to_arm[measure_x86]))
    ctx()
    w.append(A.ldr(8, 9, 0x10))
    w.append(A.add_imm(8, 8, 4))
    w.append(A.str_(8, 9, 0x10))                                    # pop it
    w.append(A.ldr(10, 9, 0))                                       # eax: width
    w += _mov32(11, k)
    w.append(A.sub_reg(10, 11, 10))
    w.append(A.lsr(10, 10, 1))
    w.append(A.ldr64(0, 31, 0))                                     # the slot
    w.append(A.str_(10, 0, 0))
    w.append(A.ldr64(1, 31, 0xa0))
    w.append(A.msr_nzcv(1))
    for r in range(0, 18, 2):
        w.append(A.ldp64_off(r, r + 1, 31, 8 * r))
    w.append(A.ldp64_off(18, 30, 31, 144))
    w.append(A.add_imm64(31, 31, 0xb0))
    w.append(A.b(pc(), site + 4))
    return w


def store_cave(at, site, old, f, x86_to_arm=None):
    """`str/strh/strb wN, [xM, #imm]` at `site` stores f(wN); temps saved."""
    if f[0] == 'centre':
        return _centre_store(at, site, old, f, x86_to_arm)
    n, rn = old & 31, (old >> 5) & 31
    sz = STORE_SIZES[old & 0xFFC00000]
    t, u = [r for r in (9, 10, 11, 12) if r not in (n, rn)][:2]
    w = [A.stp64_pre(t, u, 31, -16), A.mov_reg(t, n)]
    if sz < 4:
        w.append(A.and_mask(t, t, 8 * sz))
    if f[0] == 'const':
        w += _mov32(t, f[1])
    elif f[0] == 'add':
        w += _mov32(u, f[1])
        w.append(A.add_reg(t, t, u))
    elif f[0] == 'map':
        fix = []
        for a, v in f[1]:
            w += _mov32(u, a)
            w.append(A.cmp_reg(t, u))
            k = len(w)
            w.append(0)
            w += _mov32(t, v)
            fix.append(len(w))
            w.append(0)
            w[k] = A.bcond(at + 4 * k, at + 4 * len(w), 1)             # b.ne next
        for k in fix:
            w[k] = A.b(at + 4 * k, at + 4 * len(w))
    elif f[0] == 'addb':
        w.append(A.add_imm(u, t, f[1]))
        w.append(A.bfi(t, u, 0, 8))
    elif f[0] == 'affd':
        _, num, den, o1, n1 = f
        for x, op in ((o1, A.sub_reg), (num, A.mul), (den, SDIV), (n1, A.add_reg)):
            w += _mov32(u, x)
            w.append(op(t, t, u))
    else:
        _, num, sh, o1, n1 = f
        w += _mov32(u, o1)
        w.append(A.sub_reg(t, t, u))
        if num != 1:
            w += _mov32(u, num)
            w.append(A.mul(t, t, u))
        if sh:
            w.append(A.asr(t, t, sh))
        w += _mov32(u, n1)
        w.append(A.add_reg(t, t, u))
    w += [(old & ~31) | t, A.ldp64_post(t, u, 31, 16)]
    w.append(A.b(at + 4 * len(w), site + 4))
    return w


def _cursor_tables(w, pc):
    """ESUI's 0x914529 (battle cursor, Transparency2): from the case byte
    [0x6D0005 + [0x91EF9C]]: >2 -> [91C3DB]=2, ==1 -> [91C3DB]=1; then
    [91C6D3] = [91C63B] = 2 if [91CFC5] == 1 else 1. Uses w11/w12."""
    w += _mov32(0, 0x91EF9C)
    w.append(A.bl(pc(), TRANSLATE))
    w.append(A.ldrsh(11, 0, 0))
    w += _mov32(0, 0x6D0005)
    w.append(A.add_reg(0, 0, 11))
    w.append(A.bl(pc(), TRANSLATE))
    w.append(A.ldrb(11, 0, 0))
    w.append(A.cmp_imm(11, 2))
    j_big = len(w)
    w.append(0)                                         # b.hi big
    w.append(A.cmp_imm(11, 1))
    j_skip = len(w)
    w.append(0)                                         # b.ne skip
    w.append(A.movz(12, 1))
    j_st = len(w)
    w.append(0)                                         # b store
    big = len(w)
    w.append(A.movz(12, 2))
    store = len(w)
    w += _mov32(0, 0x91C3DB)
    w.append(A.bl(pc(), TRANSLATE))
    w.append(A.strb(12, 0, 0))
    skip = len(w)
    w += _mov32(0, 0x91CFC5)
    w.append(A.bl(pc(), TRANSLATE))
    w.append(A.ldrb(11, 0, 0))
    w.append(A.movz(12, 1))
    w.append(A.cmp_imm(11, 1))
    j_ne = len(w)
    w.append(0)                                         # b.ne keep 1
    w.append(A.movz(12, 2))
    keep = len(w)
    for addr in (0x91C6D3, 0x91C63B):
        w += _mov32(0, addr)
        w.append(A.bl(pc(), TRANSLATE))
        w.append(A.strb(12, 0, 0))
    return {j_big: (big, 8), j_skip: (skip, 1), j_st: (store, None), j_ne: (keep, 1)}


def hook_cave(at, site, displaced, ops):
    w = []
    pc = lambda: at + 4 * len(w)                                  # noqa: E731
    w.append(A.stp64_pre(0, 1, 31, -64))
    w.append(A.stp64_off(8, 9, 31, 16))
    w.append(A.stp64_off(10, 30, 31, 32))
    w.append(A.stp64_off(11, 12, 31, 48))
    w.append(A.mrs_nzcv(1))
    skips = []
    for op in ops:
        kind = op[0]
        if kind == 'skip_lt8':                   # the ops after it run only when byte [addr] >= val
            _, addr, val = op
            w += _mov32(0, addr)
            w.append(A.bl(pc(), TRANSLATE))
            w.append(A.ldrb(8, 0, 0))
            w.append(A.cmp_imm(8, val))
            skips.append(len(w))
            w.append(0)                                   # b.lo the end of the ops
        elif kind in ('st8', 'st32'):
            _, addr, val = op
            w += _mov32(0, addr)
            w.append(A.bl(pc(), TRANSLATE))
            w += _mov32(8, val)
            w.append(A.strb(8, 0, 0) if kind == 'st8' else A.str_(8, 0, 0))
        elif kind == 'cursor_tables':
            base = len(w)
            fix = _cursor_tables(w, pc)
            for k, (t, cond) in fix.items():
                frm, to = at + 4 * k, at + 4 * t
                w[k] = A.b(frm, to) if cond is None else A.bcond(frm, to, cond)
            del base
        else:
            raise ValueError('hook op %r' % (op,))
    for k in skips:
        w[k] = A.bcond(at + 4 * k, at + 4 * len(w), 3)    # lo
    w.append(A.msr_nzcv(1))
    w.append(A.ldp64_off(11, 12, 31, 48))
    w.append(A.ldp64_off(10, 30, 31, 32))
    w.append(A.ldp64_off(8, 9, 31, 16))
    w.append(A.ldp64_post(0, 1, 31, 64))
    w.append(displaced)
    w.append(A.b(pc(), site + 4))
    return w


def call_cave(at, site, displaced, callee_arm, args):
    """BUILD 669: a guest call `callee(args...)` (cdecl, constant args) made
    before the displaced word. Every caller-saved host register, x30 and the
    flags are kept; the guest stack is left as it was (the callee pops its
    return slot, the cave pops the arguments)."""
    w = []
    pc = lambda: at + 4 * len(w)                                  # noqa: E731
    w.append(A.sub_imm64(31, 31, 0xb0))
    for r in range(0, 18, 2):
        w.append(A.stp64_off(r, r + 1, 31, 8 * r))
    w.append(A.stp64_off(18, 30, 31, 144))
    w.append(A.mrs_nzcv(1))
    w.append(A.str64(1, 31, 0xa0))

    def ctx():
        w.append(A.adrp(9, pc(), CTX_PTR))
        w.append(A.ldr64(9, 9, CTX_PTR & 0xFFF))
    for v in reversed(args):
        ctx()
        w.append(A.ldr(8, 9, 0x10))
        w.append(A.sub_imm(8, 8, 4))
        w.append(A.str_(8, 9, 0x10))
        w.append(A.mov_reg(0, 8))
        w.append(A.bl(pc(), TRANSLATE))
        w += _mov32(10, v)
        w.append(A.str_(10, 0, 0))
    ctx()
    w.append(A.ldr(8, 9, 0x10))
    w.append(A.sub_imm(8, 8, 4))
    w.append(A.str_(8, 9, 0x10))                                    # return slot
    w.append(A.bl(pc(), callee_arm))
    ctx()
    w.append(A.ldr(8, 9, 0x10))
    w.append(A.add_imm(8, 8, 4 * len(args)))
    w.append(A.str_(8, 9, 0x10))                                    # pop the args
    w.append(A.ldr64(1, 31, 0xa0))
    w.append(A.msr_nzcv(1))
    for r in range(0, 18, 2):
        w.append(A.ldp64_off(r, r + 1, 31, 8 * r))
    w.append(A.ldp64_off(18, 30, 31, 144))
    w.append(A.add_imm64(31, 31, 0xb0))
    w.append(displaced)
    w.append(A.b(pc(), site + 4))
    return w


GOBJ_WINDOW_FILL = 0xDC0FF0       # the menu's opaque window fills (untextured quads)
FILL_FLAG = 0xDC3CE0              # set by the game's own fill routine after a fill
X86_ALLOC = 0x66E272              # (count, gobj) -> eax != 0; [gobj+0x70] = the quad


def _f32(v):
    return struct.unpack('<I', struct.pack('<f', v))[0]


def backdrop_cave(at, site, displaced, alloc_arm, rects, z, colour):
    """BUILD 669b: opaque quads queued in the window-fill object before the
    displaced word -- the game's own window-fill vertices (6EB1EF: x, y, z,
    rhw 1, colour, specular FF000000; no texture), here one colour and a far
    z. Every caller-saved host register, x30 and the flags are kept; the
    guest stack is left as it was."""
    w = []
    pc = lambda: at + 4 * len(w)                                  # noqa: E731
    w.append(A.sub_imm64(31, 31, 0xc0))
    for r in range(0, 18, 2):
        w.append(A.stp64_off(r, r + 1, 31, 8 * r))
    w.append(A.stp64_off(18, 30, 31, 144))
    w.append(A.mrs_nzcv(1))
    w.append(A.str64(1, 31, 0xa0))

    def ctx():
        w.append(A.adrp(9, pc(), CTX_PTR))
        w.append(A.ldr64(9, 9, CTX_PTR & 0xFFF))

    def push(load):
        load()                                                      # value -> w10
        w.append(A.str_(10, 31, 0xb0))
        ctx()
        w.append(A.ldr(8, 9, 0x10))
        w.append(A.sub_imm(8, 8, 4))
        w.append(A.str_(8, 9, 0x10))
        w.append(A.mov_reg(0, 8))
        w.append(A.bl(pc(), TRANSLATE))
        w.append(A.ldr(10, 31, 0xb0))
        w.append(A.str_(10, 0, 0))
    ends = []
    ctx()
    w.append(A.ldr(10, 9, 0))
    w.append(A.ldr(11, 9, 4))
    w.append(A.ldr(12, 9, 8))
    w.append(A.str_(10, 31, 0xb4))
    w.append(A.str_(11, 31, 0xb8))
    w.append(A.str_(12, 31, 0xbc))                                 # guest eax, ecx, edx
    for x0, y0, x1, y1 in rects:
        # gobj = [0xDC0FF0]; none yet -> nothing to queue into
        w += _mov32(0, GOBJ_WINDOW_FILL)
        w.append(A.bl(pc(), TRANSLATE))
        w.append(A.ldr(10, 0, 0))
        w.append(A.str_(10, 31, 0xa8))
        ends.append(len(w))
        w.append(('cbz', 10))
        push(lambda: w.append(A.ldr(10, 31, 0xa8)))
        push(lambda: w.extend(_mov32(10, 1)))
        ctx()
        w.append(A.ldr(8, 9, 0x10))
        w.append(A.sub_imm(8, 8, 4))
        w.append(A.str_(8, 9, 0x10))                                # return slot
        w.append(A.bl(pc(), alloc_arm))
        ctx()
        w.append(A.ldr(8, 9, 0x10))
        w.append(A.add_imm(8, 8, 8))
        w.append(A.str_(8, 9, 0x10))                                # pop the args
        w.append(A.ldr(8, 9, 0))                                    # eax
        ends.append(len(w))
        w.append(('cbz', 8))                                        # no quad -> end
        w.append(A.ldr(10, 31, 0xa8))
        w.append(A.add_imm(0, 10, 0x70))
        w.append(A.bl(pc(), TRANSLATE))
        w.append(A.ldr(10, 0, 0))
        w.append(A.str_(10, 31, 0xac))                              # the quad (guest)
        for k, (x, y) in enumerate(((x0, y0), (x0, y1), (x1, y0), (x1, y1))):
            w.append(A.ldr(10, 31, 0xac))
            w.append(A.add_imm(0, 10, 0x20 * k))
            w.append(A.bl(pc(), TRANSLATE))
            for j, v in enumerate((_f32(x), _f32(y), _f32(z), _f32(1.0), colour, 0xFF000000)):
                w += _mov32(10, v)
                w.append(A.str_(10, 0, 4 * j))
        w += _mov32(0, FILL_FLAG)
        w.append(A.bl(pc(), TRANSLATE))
        w += _mov32(10, 1)
        w.append(A.str_(10, 0, 0))
    for k in ends:
        w[k] = A.cbz(w[k][1], at + 4 * k, at + 4 * len(w))           # skip the rest
    ctx()
    w.append(A.ldr(10, 31, 0xb4))
    w.append(A.ldr(11, 31, 0xb8))
    w.append(A.ldr(12, 31, 0xbc))
    w.append(A.str_(10, 9, 0))
    w.append(A.str_(11, 9, 4))
    w.append(A.str_(12, 9, 8))
    w.append(A.ldr64(1, 31, 0xa0))
    w.append(A.msr_nzcv(1))
    for r in range(0, 18, 2):
        w.append(A.ldp64_off(r, r + 1, 31, 8 * r))
    w.append(A.ldp64_off(18, 30, 31, 144))
    w.append(A.add_imm64(31, 31, 0xc0))
    w.append(displaced)
    w.append(A.b(pc(), site + 4))
    return w


def replace_words(at, site, words):
    """A word replaced by a sequence; entries are words, or ('b'|'bl', abs
    target) / ('T',) (bl TRANSLATE) / ('bcond', +words, cond), resolved at `at`."""
    ws = []
    for k, x in enumerate(list(words) + [('b', site + 4)]):
        here = at + 4 * k
        if isinstance(x, int):
            ws.append(x)
        elif x[0] == 'T':
            ws.append(A.bl(here, TRANSLATE))
        elif x[0] == 'bl':
            ws.append(A.bl(here, x[1]))
        elif x[0] == 'bcond':                     # (bcond, +words, cond), within the sequence
            ws.append(A.bcond(here, here + 4 * x[1], x[2]))
        else:
            ws.append(A.b(here, x[1]))
    return ws


def _pc_relative(word):
    return ((word & 0x1F000000) == 0x10000000            # adr/adrp
            or (word & 0x7C000000) == 0x14000000         # b/bl
            or (word & 0xFF000010) == 0x54000000         # b.cond
            or (word & 0x7E000000) == 0x34000000         # cbz/cbnz
            or (word & 0x7E000000) == 0x36000000)        # tbz/tbnz


def build(port, read_word, x86_to_arm, base):
    """{va: new word} for one function's port, caves laid out from `base`.
    Returns (words, next free address)."""
    out = {}
    for va, (old, new) in port.get('words', {}).items():
        out[va] = new
    at = base

    def place(site, ws):
        nonlocal at
        if site in out:
            raise ValueError('+0x%X edited twice' % site)
        for k, x in enumerate(ws):
            out[at + 4 * k] = x
        out[site] = A.b(site, at) if site not in port.get('bl_sites', ()) else A.bl(site, at)
        at += 4 * len(ws)
    for site, (callee_x86, tr, post) in sorted(port.get('sites', {}).items()):
        tr = {int(j): tuple(f) for j, f in tr.items()}
        ws = callsite_cave(at, x86_to_arm[callee_x86], tr, [tuple(p) for p in post], x86_to_arm)
        for k, x in enumerate(ws):
            out[at + 4 * k] = x
        if site in out and out[site] != A.bl(site, x86_to_arm[callee_x86]):
            # a word edit may retarget the call; the cave then carries it on
            raise ValueError('+0x%X edited twice' % site)
        out[site] = A.bl(site, at)
        at += 4 * len(ws)
    for site, ops in port.get('hooks', []):
        disp = read_word(site)
        if _pc_relative(disp):
            raise ValueError('hook +0x%X: pc-relative word' % site)
        place(site, hook_cave(at, site, disp, [tuple(o) for o in ops]))
    for site, callee_x86, args, expect in port.get('calls', []):
        disp = read_word(site)
        if disp != expect:
            raise ValueError('call hook +0x%X: word %08X, expected %08X' % (site, disp, expect))
        if _pc_relative(disp):
            raise ValueError('call hook +0x%X: pc-relative word' % site)
        place(site, call_cave(at, site, disp, x86_to_arm[callee_x86], tuple(args)))
    for site, expect, rects, z, colour in port.get('backdrop', []):
        disp = read_word(site)
        if disp != expect:
            raise ValueError('backdrop +0x%X: word %08X, expected %08X' % (site, disp, expect))
        if _pc_relative(disp):
            raise ValueError('backdrop +0x%X: pc-relative word' % site)
        place(site, backdrop_cave(at, site, disp, x86_to_arm[X86_ALLOC], rects, z, colour))
    for site, words in port.get('replace', []):
        place(site, replace_words(at, site, [tuple(x) if isinstance(x, (list, tuple)) else x
                                             for x in words]))
    for site in port.get('skip_calls', []):
        ws = [A.stp64_pre(9, 10, 31, -16), A.adrp(9, at + 4, CTX_PTR),
              A.ldr64(9, 9, CTX_PTR & 0xFFF), A.ldr(10, 9, 0x10),
              A.add_imm(10, 10, 4), A.str_(10, 9, 0x10), A.ldp64_post(9, 10, 31, 16)]
        ws.append(A.b(at + 4 * len(ws), site + 4))
        place(site, ws)
    for site, f in sorted(port.get('stores', {}).items()):
        old = read_word(site)
        if (old & 0xFFC00000) not in STORE_SIZES:
            raise ValueError('store +0x%X: not a str/strh/strb' % site)
        place(site, store_cave(at, site, old, tuple(f), x86_to_arm))
    for site, val in port.get('store_const', []):
        ws = [A.stp64_pre(9, 10, 31, -16)] + _mov32(9, val) + \
             [A.str_(9, 0, 0), A.ldp64_post(9, 10, 31, 16)]
        ws.append(A.b(at + 4 * len(ws), site + 4))
        place(site, ws)
    return out, at
