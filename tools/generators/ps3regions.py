#!/usr/bin/env python3
"""ps3regions.py -- BUILD 868. Checks every region in ff7nx_ps3regions
against the stock module and the modded x86, then writes the blobs' sources
native/ps3tweaks<blob>.S and native/ps3tweaks<blob>_c.c (rebuild with
native/build_ps3tweaks.sh). `--check` only checks.

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [os.path.join(ROOT, 'src'), os.path.join(ROOT, 'src', 'core'),
                HERE]
import nxpaths  # noqa: E402
nxpaths.install()
import capstone  # noqa: E402
import exe_patch  # noqa: E402
import x86c  # noqa: E402

CALLER_SAVED = {'x%d' % n for n in range(19)} | {'nzcv'}
FLAG_WRITERS = {'cmp', 'cmn', 'tst', 'adds', 'subs', 'ands', 'bics', 'negs',
                'ccmp', 'ccmn', 'fcmp', 'fcmpe', 'adcs', 'sbcs', 'ngcs'}
FLAG_READERS = {'csel', 'csinc', 'csinv', 'csneg', 'cset', 'csetm', 'cinc',
                'cinv', 'cneg', 'ccmp', 'ccmn', 'adc', 'sbc', 'adcs', 'sbcs',
                'ngc', 'ngcs', 'fcsel'}


class Refused(Exception):
    pass


def _norm(name):
    if name in ('wzr', 'xzr', 'sp', 'wsp', 'x29', 'x30', 'w29', 'w30',
                'fp', 'lr'):
        return None
    if name[0] in 'wx' and name[1:].isdigit():
        return 'x' + name[1:]
    return name


class Arm(object):
    _n = staticmethod(lambda r: _norm(r))

    def __init__(self, module):
        self.m = module
        self.md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
        self.md.detail = True
        self.cache = {}

    def insn(self, a):
        if a not in self.cache:
            got = list(self.md.disasm(self.m.img[a:a + 4], a))
            self.cache[a] = got[0] if got else None
        return self.cache[a]

    def flow(self, a):
        i = self.insn(a)
        if i is None:
            return []
        mn = i.mnemonic
        if mn in ('ret', 'br', 'brk', 'hlt') or mn.startswith('udf'):
            return []
        if mn == 'b':
            return [i.operands[-1].imm]
        if mn.startswith('b.') or mn in ('cbz', 'cbnz', 'tbz', 'tbnz'):
            return [i.operands[-1].imm, a + 4]
        return [a + 4]

    def rw(self, a):
        i = self.insn(a)
        if i is None:
            return set(), set()
        try:
            r, w = i.regs_access()
        except capstone.CsError:
            r, w = (), ()
        rd = {_norm(i.reg_name(x)) for x in r} - {None}
        wr = {_norm(i.reg_name(x)) for x in w} - {None}
        mn = i.mnemonic
        if mn in FLAG_WRITERS:
            wr.add('nzcv')
        if mn in FLAG_READERS or mn.startswith('b.'):
            rd.add('nzcv')
        if mn in ('bl', 'blr'):
            wr |= CALLER_SAVED
        return rd, wr


CTX_PAGE, CTX_OFF = 0x12CE000, 0x2B0          # the port's context pointer


def check_ctx(module, lo, hi, reg, name):
    """Refused unless x<reg> holds the context all through lo..hi: loaded
    from the port's pointer (adrp + ldr) and written nowhere else."""
    arm, want, loads = Arm(module), 'x%d' % reg, 0
    for a in range(lo, hi, 4):
        i = arm.insn(a)
        if i is None or want not in arm.rw(a)[1]:
            continue
        if i.mnemonic == 'adrp' and i.op_str == '%s, #0x%x' % (want,
                                                                CTX_PAGE):
            continue
        if i.mnemonic == 'ldr' and i.op_str == '%s, [%s, #0x%x]' % (
                want, want, CTX_OFF):
            loads += 1
            continue
        if i.mnemonic in ('ldp', 'ldr') and '[sp' in i.op_str:
            continue                    # the epilogue's restore
        raise Refused('%s: +0x%X writes %s' % (name, a, want))
    if not loads:
        raise Refused('%s: %s is not the context' % (name, want))


def check(module, read_x86, region):
    """Refused, or the ARM span the region replaces."""
    arm = Arm(module)
    hooks = [h for _x, h in region.entries]
    resumes = set(region.exits.values()) | set(region.bounds.values())
    x86_fn = module.containing(region.entries[0][0])
    lo, hi = module.extent(x86_fn[0])
    check_ctx(module, lo, hi, region.ctx, region.name)
    for x86, h in region.entries:
        if module.containing(x86) != x86_fn:
            raise Refused('%s: entries in different functions' % region.name)
        if not lo <= h < hi:
            raise Refused('%s: hook +0x%X outside the body' % (region.name,
                                                               h))
    for r in resumes:
        if not lo <= r < hi:
            raise Refused('%s: resume +0x%X outside the body'
                          % (region.name, r))
    # the replaced span
    span, todo = set(), list(hooks)
    while todo:
        a = todo.pop()
        if a in span or a in resumes:
            continue
        if not lo <= a < hi:
            raise Refused('%s: replaced code leaves the body at +0x%X'
                          % (region.name, a))
        span.add(a)
        todo += arm.flow(a)
    # entered from nowhere else
    for a in range(lo, hi, 4):
        if a in span:
            continue
        for t in arm.flow(a):
            if t in span and t not in hooks:
                raise Refused('%s: +0x%X enters the replaced code at +0x%X'
                              % (region.name, a, t))
    # what the resume points may read: what the port's code sets on its
    # way from a hook to that resume (the region runs in its place)
    for r in set(region.exits.values()):
        on_way, todo = set(), [a for a in span if r in arm.flow(a)]
        while todo:
            a = todo.pop()
            if a in on_way:
                continue
            on_way.add(a)
            todo += [p for p in span if a in arm.flow(p)]
        stale = set()
        for a in on_way:
            stale |= arm.rw(a)[1]
        stale.add('nzcv')           # the entry's own compares
        stale -= {_norm(x) for x in region.fixed}
        seen, todo = set(), [(r, frozenset(stale))]
        steps = 0
        while todo:
            a, st = todo.pop()
            if not st or (a, st) in seen or not lo <= a < hi:
                continue
            seen.add((a, st))
            steps += 1
            if steps > 20000:
                raise Refused('%s: resume +0x%X: scan too long'
                              % (region.name, r))
            rd, wr = arm.rw(a)
            bad = rd & st
            if bad:
                raise Refused('%s: resume +0x%X: +0x%X reads %s, which the '
                              'replaced code set' % (region.name, r, a,
                                                     sorted(bad)))
            st = st - wr
            for t in arm.flow(a):
                todo.append((t, st))
    # the x86: every way into the ranges is an entry
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    inside = lambda va: any(a <= va < b for a, b in region.ranges)
    entries = {x for x, _h in region.entries}
    fs, fe = x86_fn
    for i in md.disasm(read_x86(fs, fe - fs), fs):
        if inside(i.address):
            continue
        targets = [i.address + i.size]
        if i.mnemonic in ('jmp', 'ret') or i.mnemonic.startswith('ret'):
            targets = []
        if (i.mnemonic == 'jmp' or i.mnemonic in x86c.JCC) and \
                i.op_str.startswith('0x'):
            targets.append(int(i.op_str, 16))
        for t in targets:
            if inside(t) and t not in entries:
                raise Refused('%s: x86 0x%X enters at 0x%X, not an entry'
                              % (region.name, i.address, t))
    return span


BRANCHES = {'b', 'bl', 'br', 'blr', 'ret', 'cbz', 'cbnz', 'tbz', 'tbnz',
            'adr', 'adrp', 'svc', 'brk', 'hlt'}


def check_hook(module, hook):
    """Refused, or the stock word the hook's blob entry runs after its C."""
    arm = Arm(module)
    span = module.containing(hook.x86)
    lo, hi = module.extent(span[0]) if span else (0, 0)
    h = hook.hook
    if not lo < h < hi:
        raise Refused('%s: hook +0x%X outside the body' % (hook.name, h))
    prev, i = arm.insn(h - 4), arm.insn(h)
    if prev is None or prev.mnemonic != 'bl':
        raise Refused('%s: +0x%X does not follow a bl' % (hook.name, h))
    if i is None or i.mnemonic in BRANCHES or i.mnemonic.startswith('b.') \
            or (i.mnemonic.startswith('ldr') and '#0x' in i.op_str
                and '[' not in i.op_str):
        raise Refused('%s: +0x%X (%s) cannot be moved' % (
            hook.name, h, i.mnemonic if i else '?'))
    rd, wr = arm.rw(h)
    words = i.op_str.replace('[', ' ').replace(']', ' ').replace(',', ' ')
    if 'nzcv' in rd or {'x29', 'x30', 'sp', 'fp', 'lr', 'w29', 'w30',
                        'wsp'} & set(words.split()):
        raise Refused('%s: +0x%X (%s %s) cannot be moved'
                      % (hook.name, h, i.mnemonic, i.op_str))
    for a in range(lo, hi, 4):
        if a != h - 4 and h in arm.flow(a):
            raise Refused('%s: +0x%X branches to the hook' % (hook.name, a))
    if not 19 <= hook.ctx <= 28:
        raise Refused('%s: x%d is not callee-saved' % (hook.name, hook.ctx))
    check_ctx(module, lo, hi, hook.ctx, hook.name)
    return struct.unpack_from('<I', module.img, h)[0], i


def dead_after(module, lo, hi, at, regs, name):
    """Refused unless every path from `at` writes `regs` before reading them
    (within lo..hi; leaving the function, or a bl, ends a path)."""
    arm = Arm(module)
    seen, todo, steps = set(), [(at, frozenset(regs))], 0
    while todo:
        a, st = todo.pop()
        if not st or (a, st) in seen or not lo <= a < hi:
            continue
        seen.add((a, st))
        steps += 1
        if steps > 20000:
            raise Refused('%s: scan too long' % name)
        rd, wr = arm.rw(a)
        if rd & st:
            raise Refused('%s: +0x%X reads %s' % (name, a, sorted(rd & st)))
        st = st - wr
        for t in arm.flow(a):
            todo.append((t, st))


def check_word(module, w):
    """Refused, or the stub's lines: the word it replaces must follow a bl
    within the block (x30 is the port's scratch there), the stub may not
    write the flags, and the registers it writes must be dead after it."""
    arm = Arm(module)
    span = module.containing(w.x86)
    lo, hi = module.extent(span[0]) if span else (0, 0)
    if not lo < w.arm < hi:
        raise Refused('%s: +0x%X outside the body' % (w.name, w.arm))
    a = w.arm - 4
    while a >= max(lo, w.arm - 0x20):
        i = arm.insn(a)
        if i is None or i.mnemonic in BRANCHES - {'bl'} or \
                i.mnemonic.startswith('b.'):
            break
        if i.mnemonic == 'bl':
            break
        a -= 4
    else:
        a = None
    if a is None or arm.insn(a) is None or arm.insn(a).mnemonic != 'bl':
        raise Refused('%s: no bl just before +0x%X' % (w.name, w.arm))
    for b in range(a + 4, w.arm + 4, 4):
        for t in range(lo, hi, 4):
            if t != b - 4 and b in arm.flow(t):
                raise Refused('%s: +0x%X is a branch target' % (w.name, b))
    if any(line.split()[0] in FLAG_WRITERS for line in w.asm):
        raise Refused('%s: the stub writes the flags' % w.name)
    dead_after(module, lo, hi, w.arm + 4,
               {Arm._n(r) for r in w.clobbers}, w.name)
    return w.asm


def fn_ref(calls, m):
    """X86(0x...) / ARM(0x...) in hook C as the trampoline they go through"""
    va = int(m.group(2), 16)
    key = va if m.group(1) == 'X86' else ('arm', va)
    if key not in calls:
        calls.append(key)
    return '((fn_t)(fns + %d))' % (4 * calls.index(key))


def reader(code):
    """read(va, n) of the stock ff7_en with `code` ({va: bytes}) applied"""
    import build
    exe = bytearray(open(build.find_game_dump(nxpaths.ROOT).exe,
                         'rb').read())
    pe = exe_patch.parse_pe(exe)
    cave = {}                   # bytes a mod writes outside .text
    for va, b in code.items():
        if 0x401000 <= va < 0x7B6000:
            o = exe_patch.va_to_offset(pe, va)
            exe[o:o + len(b)] = b
        else:
            for k, c in enumerate(b):
                cave[va + k] = c

    def read(va, n):
        if va in cave:
            out = bytearray()
            while len(out) < n and va + len(out) in cave:
                out.append(cave[va + len(out)])
            return bytes(out)
        o = exe_patch.va_to_offset(pe, va)
        return bytes(exe[o:o + n])
    return read


GO = """
/* every entry: x0 = the context, w3 = which entry, x5 = its resume table,
 * x6 = its C, the host registers saved (x4 points at x0..x17). The C
 * returns the exit; the bl's return address moves to that exit's resume. */
.Lx3_go:
        adr     x1, .Lg2h
        adr     x2, .Lx3_fns
        mov     x4, sp
        str     x5, [sp, #0x90]
        blr     x6
        ldr     x5, [sp, #0x90]
        ldrsw   x1, [x5, w0, uxtw #2]
        ldr     x30, [sp, #0x228]
        add     x30, x30, x1
        str     x30, [sp, #0x228]
        bl      .Lrestore
        ldp     x29, x30, [sp], #0x10
        ret
"""


def generate(blob, groups, module):
    """(S text, C text) of one blob: groups is [(regions, code)]"""
    import re
    import ff7nx_ps3regions as S
    calls, cs, ss = [], [], []
    # the blob's data areas: one word each, just before .Lx3_fns
    dsyms = []
    for regions, _code in groups:
        for reg in regions:
            if reg.blob == blob and isinstance(reg, S.R):
                for d in reg.data.values():
                    if isinstance(d, tuple) and d[0] not in dsyms:
                        dsyms.append(d[0])
                for c in reg.pre.values():
                    for sym, _k in re.findall(r'DATA\((\w+), *(\d+)\)', c):
                        if sym not in dsyms:
                            dsyms.append(sym)

    import ff7nx_ps3tweaks as P
    # the blob's tables (constant: the mod's bytes), before the data areas
    tabs, tabmap = [], {}       # [(lo, bytes)]; (region, lo) -> one
    for regions, code in groups:
        rd = None
        for reg in regions:
            if reg.blob == blob and isinstance(reg, S.R) and reg.tables:
                rd = rd or reader(code)
                for lo, hi in reg.tables:
                    b = bytes(rd(lo, hi - lo))
                    tabmap[(reg.name, lo)] = (lo, b)
                    if (lo, b) not in tabs:     # one copy of each
                        tabs.append((lo, b))

    def tsize(b):
        return (len(b) + 3) & ~3

    def dsize(sym):
        return (P.DATA.get(sym, 4) + 3) & ~3

    def dexpr(sym, k):
        if not 0 <= k < dsize(sym):
            raise Refused('%s byte %d outside its data area' % (sym, k))
        at = sum(dsize(x) for x in dsyms[:dsyms.index(sym)])
        return '((u8 *)fns)[%d]' % (at - sum(map(dsize, dsyms)) + k)

    def texpr(name, lo):
        j = tabs.index(tabmap[(name, lo)])
        back = sum(tsize(b) for _l, b in tabs[j:]) + \
            sum(map(dsize, dsyms))
        return '((u8 *)fns - %d)' % back
    for regions, code in groups:
        read = reader(code)
        for reg in regions:
            if reg.blob != blob:
                continue
            if isinstance(reg, S.W):
                sym = 'x3w_%s' % reg.name
                ss.append('/* %s: ARM +0x%X (x86 0x%X) */'
                          % (reg.name, reg.arm, reg.x86))
                ss.append('        .global %s\n%s:' % (sym, sym))
                ss += ['        ' + line for line in check_word(module, reg)]
                ss.append('        ret')
                continue
            if isinstance(reg, S.H):
                word, i = check_hook(module, reg)
                body = re.sub(r'\b(X86|ARM)\((0x[0-9A-Fa-f]+)\)',
                              lambda m: fn_ref(calls, m), reg.c)
                cs.append('/* %s: hook at ARM +0x%X (x86 0x%X) */\n'
                          'PT static void x3hc_%s(u32 *ctx, g2h_t g2h, '
                          'char *fns, u64 *regs)\n{%s}\n'
                          % (reg.name, reg.hook, reg.x86, reg.name, body))
                sym = 'x3h_%s' % reg.name
                ss.append('/* %s: hook at ARM +0x%X; %s its word, %s %s */'
                          % (reg.name, reg.hook, 'then' if reg.replay
                             else 'in place of', i.mnemonic, i.op_str))
                ss.append('        .global %s\n%s:' % (sym, sym))
                ss.append('        stp     x29, x30, [sp, #-0x10]!')
                ss.append('        bl      .Lsave')
                ss.append('        mov     x0, x%d' % reg.ctx)
                ss.append('        adr     x1, .Lg2h')
                ss.append('        adr     x2, .Lx3_fns')
                ss.append('        mov     x3, sp')
                ss.append('        bl      x3hc_%s' % reg.name)
                ss.append('        bl      .Lrestore')
                ss.append('        ldp     x29, x30, [sp], #0x10')
                if reg.replay:
                    ss.append('        .inst   0x%08X' % word)
                else:
                    ss.append('        /* the C stands in for it */')
                ss.append('        ret')
                continue
            check(module, read, reg)
            rg = x86c.Region(reg.name, read, reg.ranges,
                             [x for x, _h in reg.entries],
                             live_flags=reg.flags, calls=calls,
                             post=reg.post, native=reg.native,
                             subs=reg.subs,
                             pre={k: re.sub(r'DATA\((\w+), *(\d+)\)',
                                            lambda m: dexpr(m.group(1),
                                                            int(m.group(2))),
                                            c)
                                  for k, c in reg.pre.items()},
                             never=reg.never, move=reg.move,
                             tables={lo: (hi - lo, texpr(reg.name, lo))
                                     for lo, hi in reg.tables},
                             data={va: dexpr(*d) if isinstance(d, tuple)
                                   else '0x%Xu' % d
                                   for va, d in reg.data.items()})
            cs.append(rg.gen())
            for k, (x86, hook) in enumerate(reg.entries):
                deltas = []
                for va in rg.exits:
                    if va not in reg.exits:
                        raise Refused('%s: exit %s has no resume point'
                                      % (reg.name, va if va == 'ret'
                                         else '0x%X' % va))
                    deltas.append(reg.exits[va] - (hook + 4))
                sym = 'x3e_%s_%d' % (reg.name, k)
                ss.append('/* %s entry %d: x86 0x%X, ARM +0x%X; exits %s */'
                          % (reg.name, k, x86, hook, ', '.join(
                              '0x%X -> +0x%X' % (va, reg.exits[va])
                              for va in rg.exits)))
                ss.append('        .global %s\n%s:' % (sym, sym))
                ss.append('        stp     x29, x30, [sp, #-0x10]!')
                ss.append('        bl      .Lsave')
                ss.append('        mov     x0, x%d' % reg.ctx)
                ss.append('        mov     w3, #%d' % k)
                ss.append('        adr     x5, .L%s' % sym)
                ss.append('        adr     x6, x3_%s' % reg.name)
                ss.append('        b       .Lx3_go')
                ss.append('.L%s:' % sym)
                ss.append('        .word   %s' % ', '.join(
                    '%d' % d for d in deltas))
    for key in calls:
        if isinstance(key, tuple) and key[1] not in S.NATIVE:
            raise Refused('+0x%X is not a NATIVE routine' % key[1])
    head = ['/*',
            ' * ps3tweaks%d.S -- BUILD 868. GENERATED by tools/generators/'
            'ps3regions.py' % blob,
            ' * from src/core/ff7nx_ps3regions.py: the entries of the region '
            'C in',
            ' * ps3tweaks%d_c.c, and the trampolines it calls through. '
            'Assembled after' % blob,
            ' * ps3tweaks_head.S. Do not edit.',
            ' *',
            ' * Licensed under GPLv3',
            ' * Copyright (c) 2026 ppkantorski',
            ' */']
    if tabs:
        head.append('        .balign 4')
    for lo, b in tabs:
        head.append('        /* the mod\'s table at x86 0x%X */' % lo)
        for k in range(0, len(b), 16):
            head.append('        .byte   %s' % ', '.join(
                '0x%02X' % c for c in b[k:k + 16]))
        head.append('        .balign 4')
    for sym in dsyms:
        head.append('        .global %s\n%s:\n        .word   %s'
                    '                       /* data: the option\'s value */'
                    % (sym, sym, ', '.join(['0'] * (dsize(sym) // 4))))
    head.append('.Lx3_fns:')
    for fn in calls:
        if isinstance(fn, tuple):
            head.append('        .global x3_arm_%X\nx3_arm_%X:\n        b'
                        '       .                       /* build: b +0x%X */'
                        % (fn[1], fn[1], fn[1]))
            continue
        head.append('        .global x3_fn_%X\nx3_fn_%X:\n        b       .'
                    '                       /* build: b x86 0x%X */'
                    % (fn, fn, fn))
    s = '\n'.join(head + [GO] + ss) + '\n'
    c = '\n'.join([
        '/*',
        ' * ps3tweaks%d_c.c -- BUILD 868. GENERATED by tools/generators/'
        'ps3regions.py' % blob,
        ' * (x86c.py) from src/core/ff7nx_ps3regions.py: each region of a '
        'mod\'s',
        ' * x86 as C on the port\'s x86 context. Do not edit.',
        ' *',
        ' * Licensed under GPLv3',
        ' * Copyright (c) 2026 ppkantorski',
        ' */',
        '#include "ps3tweaks.h"', ''] + cs)
    return s, c


def groups():
    import ff7nx_ps3regions as S
    import ff7nx_ps3tweaks as P
    return [(S.TWO_ACCESSORIES, P.TWO_ACCESSORIES_CODE),
            (S.LIMIT_OVERHAUL, P.LIMIT_OVERHAUL_CODE),
            (S.ENEMY_SCALING, P.ENEMY_SCALING_CODE),
            (S.SOLO, P.SOLO_CODE),
            (S.SOLO_CAIT, P.SOLO_CAIT_CODE),
            (S.SOLO_VINCENT, P.SOLO_VINCENT_CODE),
            (S.SOLO_RANDOM, P.SOLO_RANDOM_CODE),
            (S.BATTLE_PHS, P.BATTLE_PHS_CODE),
            (S.PARTY_RANDO, P.PARTY_RANDO_CODE),
            (S.ITEM_RANDO, P.ITEM_RANDO_CODE),
            (S.BATTLE_RANDO, P.BATTLE_RANDO_CODE),
            (S.BATTLE_RANDO_ES, {**P.BATTLE_RANDO_CODE,
                                 **P.RANDO_SCALER_CODE}),
            (S.DOUBLE_BOSSES, {**P.BATTLE_RANDO_CODE,
                               **P.DOUBLE_BOSSES_CODE}),
            (S.CHARACTER_ORDER, P.CHARACTER_ORDER_CODE),
            (S.STARTING_LEVEL, P.STARTING_LEVEL_CODE)]


def main():
    import build
    import nxmap
    module = nxmap.Main(build.find_game_dump(nxpaths.ROOT).nso)
    gs = groups()
    blobs = sorted({r.blob for regions, _c in gs for r in regions})
    for b in blobs:
        s, c = generate(b, gs, module)
        if '--check' in sys.argv:
            continue
        native = os.path.join(ROOT, 'native')
        with open(os.path.join(native, 'ps3tweaks%d.S' % b), 'w') as f:
            f.write(s)
        with open(os.path.join(native, 'ps3tweaks%d_c.c' % b), 'w') as f:
            f.write(c)
        print('ps3tweaks%d: %d regions' % (b, sum(
            1 for regions, _c in gs for r in regions if r.blob == b)))


if __name__ == '__main__':
    main()
