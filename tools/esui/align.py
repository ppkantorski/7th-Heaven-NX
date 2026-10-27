#!/usr/bin/env python3
"""align.py -- map patched x86 instructions of an ESUI function onto the ARM
immediates the port compiled them to, and propose word edits.

usage: python3 align.py <fn_va> [file-prefix filter, comma list]
Prints one line per patched x86 instruction:
  status  x86va  old -> new  | arm va  old-asm -> new-asm  expect set

Licensed under GPLv3
Copyright (c) 2026 ppkantorski
"""

import os as _nx_os, sys as _nx_sys  # BUILD 755: find src/ and put its modules on sys.path
_nx_root = _nx_os.path.dirname(_nx_os.path.abspath(__file__))
while not _nx_os.path.isfile(_nx_os.path.join(_nx_root, '7th_heaven_nx.py')) and _nx_root != _nx_os.path.dirname(_nx_root):
    _nx_root = _nx_os.path.dirname(_nx_root)
_nx_sys.path.insert(0, _nx_os.path.join(_nx_root, 'src'))
import nxpaths  # noqa: E402
nxpaths.install(tools=True)
import sys, os, re, json, bisect, collections, struct
sys.path.insert(0, '.')
import exe_patch, capstone, nxmap, keystone
from capstone import arm64 as A64, x86 as X

EXE = 'dump/romfs/ff7/resources/ff7_1.02/ff7_en'
exe = open(EXE, 'rb').read()
pe = exe_patch.parse_pe(exe)
off = lambda va: exe_patch.va_to_offset(pe, va)
M = nxmap.Main('dump/exefs/main')
arm2x = {v: k for k, v in M.x86_to_arm.items()}
cx = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32); cx.detail = True
ca = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM); ca.detail = True
ks = keystone.Ks(keystone.KS_ARCH_ARM64, keystone.KS_MODE_LITTLE_ENDIAN)


def s32(v):
    v &= 0xffffffff
    return v - (1 << 32) if v & 0x80000000 else v


_NEWC = {}


def build_new(prefixes):
    k = tuple(prefixes)
    if k not in _NEWC:
        _NEWC[k] = _build_new(prefixes)
    return _NEWC[k]


def _build_new(prefixes):
    files = json.load(open('tools/esui/active_hext.json'))
    new = bytearray(exe)
    for fo, p in files:
        if fo != 'EnhancedStock':
            continue
        if not os.path.basename(p).startswith(tuple(prefixes)):
            continue
        for va, b in exe_patch.parse_hext(open(p, encoding='utf-8', errors='replace').read()):
            o = off(va)
            if o is not None:
                new[o:o + len(b)] = b
    return bytes(new)


def x86_tokens(fs, fe, buf):
    out = []
    for i in cx.disasm(buf[off(fs):off(fs) + fe - fs], fs):
        vals = []
        if i.mnemonic == 'call' and i.operands[0].type == X.X86_OP_IMM:
            vals.append(('call', i.operands[0].imm))
        else:
            for op in i.operands:
                if op.type == X.X86_OP_IMM:
                    vals.append(('imm', s32(op.imm)))
                elif op.type == X.X86_OP_MEM and op.mem.disp:
                    vals.append(('mem', s32(op.mem.disp)))
        out.append((i, vals))
    return out


ESP_STORES = ('[x20, #0x10]',)


def arm_tokens(a0, a1):
    ins = list(ca.disasm(M.text[a0:a1], a0))
    out = []          # [kind, value, [addrs], insn list, extra]
    pend = {}         # reg -> mov chain entry still being built
    live = {}         # reg -> def entry (constant register)
    for n, i in enumerate(ins):
        m = i.mnemonic; ops = i.operands
        try:
            rd, wr = i.regs_access()
        except Exception:
            rd, wr = [], []
        # uses of constant registers
        for r in rd:
            if r in live:
                d = live[r]
                d[4]['uses'].append(i.address)
                if m in ('str', 'strh', 'strb') and ops[0].type == A64.ARM64_OP_REG and ops[0].reg == r:
                    out.append(['use', d[1], [i.address], [i], {'def': d, 'reg': r}])
        if m == 'bl':
            t = ops[0].imm
            out.append(['call', arm2x.get(t, -t), [i.address], [i], {}])
            # calls clobber caller-saved regs
            for r in list(live):
                nm = i.reg_name(r)
                if nm[1:].isdigit() and int(nm[1:]) < 19:
                    live.pop(r)
            for r in list(pend):
                pend.pop(r)
            continue
        if m in ('mov', 'movz', 'movn', 'orr') and len(ops) == 2 and ops[1].type == A64.ARM64_OP_IMM \
                or (m == 'orr' and len(ops) == 3 and ops[2].type == A64.ARM64_OP_IMM and i.reg_name(ops[1].reg) in ('wzr', 'xzr')):
            v = ops[-1].imm
            e = ['imm', s32(v), [i.address], [i], {'uses': [], 'reg': ops[0].reg}]
            pend[ops[0].reg] = e; live[ops[0].reg] = e; out.append(e); continue
        if m == 'movk' and ops[0].reg in pend:
            e = pend[ops[0].reg]; sh = ops[1].shift.value or 0
            e[1] = s32((e[1] & ~(0xffff << sh)) | (ops[1].imm << sh))
            e[2].append(i.address); e[3].append(i); continue
        for r in wr:
            pend.pop(r, None); live.pop(r, None)
            # w/x aliasing
            nm = i.reg_name(r)
            for r2 in list(live):
                if i.reg_name(r2)[1:] == nm[1:]:
                    live.pop(r2)
        nxt = ' ; '.join(j.mnemonic + ' ' + j.op_str for j in ins[n + 1:n + 4])
        for op in ops:
            if op.type == A64.ARM64_OP_IMM and m not in (
                    'b', 'cbz', 'cbnz', 'tbz', 'tbnz', 'adrp', 'adr') \
                    and not m.startswith('b.') and m not in (
                    'lsl', 'lsr', 'asr', 'ubfx', 'sbfx', 'bfi', 'ubfiz', 'bfxil',
                    'ccmp', 'ccmn', 'and', 'orr', 'eor', 'tst', 'ands', 'sbfiz'):
                v = op.imm << (op.shift.value or 0)
                if m in ('sub', 'subs', 'cmn'):
                    v = -v
                if abs(v) in (4, 8, 0xc, 0x10, 0x14, 0x18, 0x1c, 0x20) and m in ('add', 'sub'):
                    rr = i.op_str.split(', ')[0]
                    if re.search(r'(str|stp) %s, (w\d+, )?\[x\d+, #0x10\]' % rr, nxt) or \
                            re.search(r'stp w\d+, %s, \[x\d+, #0xc\]' % rr, nxt):
                        continue      # esp adjustment
                out.append(['imm', s32(v), [i.address], [i], {}])
    # live register snapshot helper
    return ins, out


def const_regs_at(ins, addr):
    """{value: regname} of constant registers live just before addr."""
    live = {}; pend = {}
    for i in ins:
        if i.address >= addr:
            break
        m = i.mnemonic; ops = i.operands
        rd, wr = i.regs_access()
        if m == 'bl':
            for r in list(live):
                nm = i.reg_name(r)
                if nm[1:].isdigit() and int(nm[1:]) < 19:
                    live.pop(r)
            continue
        if m in ('mov', 'movz', 'orr') and ops[-1].type == A64.ARM64_OP_IMM and \
                (len(ops) == 2 or i.reg_name(ops[1].reg) in ('wzr',)):
            live[ops[0].reg] = s32(ops[-1].imm); DEFAT[ops[0].reg] = i.address; continue
        if m == 'movk' and ops[0].reg in live:
            sh = ops[1].shift.value or 0
            live[ops[0].reg] = s32((live[ops[0].reg] & ~(0xffff << sh)) | (ops[1].imm << sh)); continue
        for r in wr:
            live.pop(r, None)
            nm = i.reg_name(r)
            for r2 in list(live):
                if i.reg_name(r2)[1:] == nm[1:]:
                    live.pop(r2)
    return {v: (ins[0].reg_name(r), DEFAT[r]) for r, v in live.items()}


DEFAT = {}


def branch_into(ins, lo, hi):
    """branch targets in (lo, hi] whose branch lies outside [lo, hi]."""
    bad = []
    for i in ins:
        m = i.mnemonic
        if m == 'b' or m.startswith('b.') or m in ('cbz', 'cbnz', 'tbz', 'tbnz'):
            t = i.operands[-1].imm
            if lo < t <= hi and not (lo <= i.address <= hi):
                bad.append((i.address, t))
    return bad


REL = [('=', lambda v: v), ('neg', lambda v: -v), ('-1', lambda v: v - 1),
       ('+1', lambda v: v + 1), ('-o-1', lambda v: -v - 1), ('-o+1', lambda v: -v + 1)]


def relation(xv, av):
    for name, f in REL:
        if s32(f(xv)) == av:
            return name, f
    return None


def lcs(A, B, match):
    """indices pairs of a max-score alignment (simple DP)."""
    n, m = len(A), len(B)
    if n == 0 or m == 0:
        return []
    import numpy as np
    S = np.zeros((n + 1, m + 1), dtype=np.int32)
    for i in range(1, n + 1):
        a = A[i - 1]
        Si = S[i]; Sp = S[i - 1]
        for j in range(1, m + 1):
            w = match(a, B[j - 1])
            best = Sp[j] if Sp[j] > Si[j - 1] else Si[j - 1]
            if w and Sp[j - 1] + w > best:
                best = Sp[j - 1] + w
            Si[j] = best
    pairs = []
    i, j = n, m
    while i and j:
        w = match(A[i - 1], B[j - 1])
        if w and S[i, j] == S[i - 1, j - 1] + w:
            pairs.append((i - 1, j - 1)); i -= 1; j -= 1
        elif S[i - 1, j] >= S[i, j - 1]:
            i -= 1
        else:
            j -= 1
    return pairs[::-1]


def align_fn(fs, new):
    fs, fe = M.containing(fs)
    a0, a1 = M.extent(fs)
    xo = x86_tokens(fs, fe, exe)
    xn = x86_tokens(fs, fe, new)
    ains, at = arm_tokens(a0, a1)
    # flatten x86 tokens
    XT = [(k, ii, kind, v) for k, (i, vals) in enumerate(xo) for ii, (kind, v) in enumerate(vals)]
    # anchors: calls
    xc = [t for t in range(len(XT)) if XT[t][2] == 'call']
    ac = [t for t in range(len(at)) if at[t][0] == 'call']
    cp = lcs([XT[t][3] for t in xc], [at[t][1] for t in ac], lambda a, b: 2 if a == b else 0)
    anchors = [(-1, -1)] + [(xc[p], ac[q]) for p, q in cp] + [(len(XT), len(at))]
    amap = {}
    for (x0, y0), (x1, y1) in zip(anchors, anchors[1:]):
        if x0 >= 0:
            amap[x0] = y0
        A = list(range(x0 + 1, x1)); B = list(range(y0 + 1, y1))
        A = [t for t in A if XT[t][2] != 'call']
        B = [t for t in B if at[t][0] != 'call']

        def match(t, u):
            xv = XT[t][3]; av = at[u][1]
            r = relation(xv, av)
            if not r:
                return 0
            w = 3 if r[0] == '=' else 1
            if abs(xv) >= 0x20:
                w += 3
            return w
        for p, q in lcs(A, B, match):
            amap[A[p]] = B[q]
    return fs, fe, a0, a1, xo, xn, XT, at, amap, ains


def render(insn, newval):
    """new asm text for an ARM imm instruction whose value becomes newval."""
    m = insn.mnemonic; s = insn.op_str
    imm_ops = [op for op in insn.operands if op.type == A64.ARM64_OP_IMM]
    op = imm_ops[-1]
    sh = op.shift.value or 0
    parts = s.split(', ')
    if m in ('add', 'adds', 'sub', 'subs', 'cmp', 'cmn'):
        neg = m in ('sub', 'subs', 'cmn')
        v = -newval if neg else newval
        if v < 0:
            m = {'add': 'sub', 'sub': 'add', 'adds': 'subs', 'subs': 'adds',
                 'cmp': 'cmn', 'cmn': 'cmp'}[m]; v = -v
        if sh:
            if v & 0xfff:
                return None
            v >>= 12
        # rebuild
        ri = [k for k, pp in enumerate(parts) if pp.startswith('#')][0]
        parts[ri] = '#%#x' % v
        if sh:
            parts = parts[:ri + 1] + ['lsl #12']
        return m + ' ' + ', '.join(parts)
    if m in ('mov', 'movz'):
        v = newval & 0xffffffff
        reg = parts[0]
        if reg.startswith('x'):
            return None
        if v <= 0xffff:
            return 'mov %s, #%#x' % (reg, v)
        if (~v & 0xffffffff) <= 0xffff:
            return 'movn %s, #%#x' % (reg, ~v & 0xffff)
        return None
    return None


def asm(text, addr):
    enc, _ = ks.asm(text, addr)
    return struct.unpack('<I', bytes(enc))[0]


def chain_edit(e, newval):
    """(list of (addr, old asm, new asm)) for a mov/movk chain value change."""
    v = newval & 0xffffffff
    insns = e[3]
    reg = insns[0].op_str.split(', ')[0]
    if len(insns) == 1:
        t = render(insns[0], newval)
        return None if t is None else [(insns[0].address, insns[0].mnemonic + ' ' + insns[0].op_str, t)]
    if len(insns) == 2 and insns[0].mnemonic in ('mov', 'movz') and insns[1].mnemonic == 'movk':
        lo, hi = v & 0xffff, v >> 16
        return [(insns[0].address, insns[0].mnemonic + ' ' + insns[0].op_str, 'movz %s, #%#x' % (reg, lo)),
                (insns[1].address, insns[1].mnemonic + ' ' + insns[1].op_str, 'movk %s, #%#x, lsl #16' % (reg, hi))]
    return None


EDITS = []
GROUPS = {}
STRUCTS = []
CUR = {}


def main():
    fn = int(sys.argv[1], 16)
    pre = (sys.argv[2] if len(sys.argv) > 2 else '00-,01-').split(',')
    new = build_new(pre)
    if os.environ.get('BLOCKS'):
        o, _ = x86_tokens(*M.containing(fn), exe), None
        xo_ = x86_tokens(*M.containing(fn), exe); xn_ = x86_tokens(*M.containing(fn), new)
        import difflib
        f_ = lambda i: i.mnemonic + ' ' + i.op_str
        sm_ = difflib.SequenceMatcher(None, [f_(i) for i, _ in xo_], [f_(i) for i, _ in xn_], autojunk=False)
        changed = set()
        for tg, i1, i2, j1, j2 in sm_.get_opcodes():
            if tg != 'equal':
                changed |= set(range(max(0, i1 - 1), min(len(xo_), i2 + 1)))
        fs, fe, a0, a1, xo, xn, XT, at, amap, ains = align_fn_blocks(fn, new, changed)
    else:
        fs, fe, a0, a1, xo, xn, XT, at, amap, ains = align_fn(fn, new)
    CUR['ains'] = ains
    print('fn %x..%x  arm %x..%x  anchors(calls) %d' % (fs, fe, a0, a1,
          sum(1 for t in amap if XT[t][2] == 'call')))
    # per x86 instruction: which tokens map
    tok_of = collections.defaultdict(list)
    for t, (k, ii, kind, v) in enumerate(XT):
        tok_of[k].append(t)
    # count how many x86 tokens map to each ARM token (sharing)
    users = collections.Counter(amap.values())
    import difflib
    full = lambda i: i.mnemonic + ' ' + i.op_str
    sm = difflib.SequenceMatcher(None, [full(i) for i, _ in xo], [full(i) for i, _ in xn], autojunk=False)
    PAIRS = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            continue
        if tag == 'replace' and i2 - i1 == j2 - j1:
            for d in range(i2 - i1):
                PAIRS.append((i1 + d, j1 + d))
        else:
            print('STRUCT-BLOCK old %s  ->  new %s' % (
                '; '.join('%x: %s' % (xo[k][0].address, full(xo[k][0])) for k in range(i1, i2)),
                '; '.join('%x: %s' % (xn[k][0].address, full(xn[k][0])) for k in range(j1, j2))))
            STRUCTS.append((i1, i2, j1, j2))
    for k, kn in PAIRS:
        (io, vo), (inn, vn) = xo[k], xn[kn]
        head = '%x: %s %s -> %s %s' % (io.address, io.mnemonic, io.op_str, inn.mnemonic, inn.op_str)
        if io.size != inn.size or len(vo) != len(vn) or \
                [a for a, _ in vo] != [a for a, _ in vn]:
            print('STRUCT  ' + head); continue
        lines = []
        for ii, ((kind, ov), (_, nv)) in enumerate(zip(vo, vn)):
            if ov == nv:
                continue
            t = tok_of[k][ii]
            if t not in amap:
                used = set(amap.values())
                cand = [u for u in range(len(at)) if at[u][0] in ('imm', 'use') and u not in used
                        and relation(ov, at[u][1]) and relation(ov, at[u][1])[0] == '=']
                defs = {(at[u][4]['def'][2][0] if at[u][0] == 'use' else at[u][2][0]) for u in cand}
                if abs(ov) >= 0x20 and len(defs) == 1:
                    u = [u for u in cand if at[u][0] == 'imm'] or cand
                    amap[t] = u[0]
                    lines.append('UNIQ-fallback value %#x' % ov)
                else:
                    lines.append('UNMAPPED  value %#x (%d cands)' % (ov, len(cand))); continue
            e = at[amap[t]]
            r = relation(ov, e[1])
            nav = s32(r[1](nv))
            share = users[amap[t]]
            ed = None; st = 'OK'
            if e[0] == 'use':
                d = e[4]['def']
                if len(d[4]['uses']) == 1:
                    ed = chain_edit(d, nav)
                    bi = branch_into(ains, d[2][0], d[4]['uses'][0])
                    if bi:
                        st = 'DEF?branch%s' % ['%x->%x' % q for q in bi]
                else:
                    regs = const_regs_at(ains, e[2][0])
                    ui = e[3][0]
                    old_r = ui.reg_name(e[4]['reg'])
                    if nav in regs and regs[nav][0][0] == old_r[0]:
                        ed = [(ui.address, ui.mnemonic + ' ' + ui.op_str,
                               ui.mnemonic + ' ' + ui.op_str.replace(old_r, regs[nav][0], 1))]
                        bi = branch_into(ains, regs[nav][1], ui.address)
                        st = 'REGSWAP' if not bi else 'REGSWAP?branch%s' % ['%x->%x' % q for q in bi]
                    else:
                        st = 'NEEDCAVE(def %x shared by %d)' % (d[2][0], len(d[4]['uses']))
                        g = GROUPS.setdefault(d[2][0], [d, set(), set(), []])
                        g[1].add(e[2][0]); g[2].add(nav); g[3].append(io.address)
            else:
                if e[4].get('uses') is not None and len(e[4]['uses']) > 1:
                    st = 'DEFSHARED%d' % len(e[4]['uses'])
                ed = chain_edit(e, nav)
            if share > 1:
                st += '+MAP%d' % share
            if ed is None and st == 'OK':
                st = 'NOENC'
            if ed:
                for a, o, n in ed:
                    try:
                        w0 = struct.unpack_from('<I', M.text, a)[0]
                        w1 = asm(n, a)
                        lines.append('%s  arm %x  %-28s -> %-28s %08x %08x (%s)' % (st, a, o, n, w0, w1, r[0]))
                        EDITS.append((st, io.address, a, w0, w1, o, n))
                    except Exception as ex:
                        lines.append('ASMERR %x %s %s' % (a, n, ex))
            else:
                lines.append('%s  arm %x  %s  (%s, new %#x)' % (st, e[2][0], e[3][0].mnemonic + ' ' + e[3][0].op_str, r[0], nav))
                EDITS.append((st, io.address, e[2][0], None, None, e[3][0].mnemonic + ' ' + e[3][0].op_str, '%#x' % nav))
        print(head)
        for l in lines:
            print('      ' + l)


def resolve_groups():
    for da, (d, uses, navs, xs) in GROUPS.items():
        if set(d[4]['uses']) == uses and len(navs) == 1:
            nav = navs.pop()
            bi = branch_into(CUR['ains'], d[2][0], max(uses))
            if bi:
                print('GROUP? branch into def %x range: %s' % (da, ['%x->%x' % q for q in bi]))
            for a, o, n in chain_edit(d, nav):
                w0 = struct.unpack_from('<I', M.text, a)[0]; w1 = asm(n, a)
                print('GROUP  def %x for x86 %s: %s -> %s  %08x %08x' % (da, ','.join('%x' % x for x in xs), o, n, w0, w1))
                EDITS.append(('GROUP', xs[0], a, w0, w1, o, n))
            for i in range(len(EDITS) - 1, -1, -1):
                if EDITS[i][0].startswith('NEEDCAVE(def %x ' % da):
                    EDITS.pop(i)
        else:
            print('UNRESOLVED def %x uses %s patched %s navs %s' % (da, [hex(u) for u in d[4]['uses']], [hex(u) for u in uses], navs))





# ---------------------------------------------------------------- block mode
def x86_blocks(fs, fe, xo):
    targets = set()
    for i, vals in xo:
        if i.mnemonic.startswith('j') and i.operands and i.operands[0].type == X.X86_OP_IMM:
            targets.add(i.operands[0].imm)
    blocks = []; cur = []
    for k, (i, vals) in enumerate(xo):
        if i.address in targets and cur:
            blocks.append(cur); cur = []
        cur.append(k)
        if i.mnemonic.startswith('j') or i.mnemonic == 'ret':
            blocks.append(cur); cur = []
    if cur:
        blocks.append(cur)
    return blocks


def sw_block(A, at, match, ga=0.15, gx=1.0):
    """Smith-Waterman of x86 token list A against all ARM tokens; returns
    (score, pairs)."""
    n = len(A); m = len(at)
    if n == 0:
        return 0, []
    # sparse match lists per i
    H = [[0.0] * (m + 1) for _ in range(n + 1)]
    best = (0, 0, 0)
    for i in range(1, n + 1):
        a = A[i - 1]; Hi = H[i]; Hp = H[i - 1]
        for j in range(1, m + 1):
            w = match(a, j - 1)
            v = Hp[j - 1] + w if w else Hp[j - 1] - gx
            u = Hp[j] - gx
            l = Hi[j - 1] - ga
            if u > v: v = u
            if l > v: v = l
            if v < 0: v = 0
            Hi[j] = v
            if v > best[0]:
                best = (v, i, j)
    s, i, j = best
    pairs = []
    while i and j and H[i][j] > 0:
        w = match(A[i - 1], j - 1)
        if w and abs(H[i][j] - (H[i - 1][j - 1] + w)) < 1e-9:
            pairs.append((i - 1, j - 1)); i -= 1; j -= 1
        elif abs(H[i][j] - (H[i - 1][j] - gx)) < 1e-9:
            i -= 1
        elif abs(H[i][j] - (H[i][j - 1] - ga)) < 1e-9:
            j -= 1
        else:
            i -= 1; j -= 1
    return s, pairs[::-1]


def align_fn_blocks(fs, new, only_blocks_with=None):
    fs, fe = M.containing(fs)
    a0, a1 = M.extent(fs)
    xo = x86_tokens(fs, fe, exe)
    xn = x86_tokens(fs, fe, new)
    ains, at = arm_tokens(a0, a1)
    XT = [(k, ii, kind, v) for k, (i, vals) in enumerate(xo) for ii, (kind, v) in enumerate(vals)]
    tok_of = collections.defaultdict(list)
    for t, (k, ii, kind, v) in enumerate(XT):
        tok_of[k].append(t)
    amap = {}
    for blk in x86_blocks(fs, fe, xo):
        if only_blocks_with is not None and not (set(blk) & only_blocks_with):
            continue
        A = [t for k in blk for t in tok_of[k]]
        # drop jump-target immediates
        A = [t for t in A if not (xo[XT[t][0]][0].mnemonic.startswith('j'))]
        if not A:
            continue

        def match(t, u):
            kind, xv = XT[t][2], XT[t][3]
            e = at[u]
            if kind == 'call':
                return 6 if e[0] == 'call' and e[1] == xv else 0
            if e[0] == 'call':
                return 0
            r = relation(xv, e[1])
            if not r:
                return 0
            w = 2 if r[0] == '=' else 0.7
            if abs(xv) >= 0x20:
                w += 2
            return w
        s, pairs = sw_block(A, at, match)
        for p, q in pairs:
            amap[A[p]] = q
    return fs, fe, a0, a1, xo, xn, XT, at, amap, ains


if __name__ == '__main__':
    main()
    resolve_groups()
