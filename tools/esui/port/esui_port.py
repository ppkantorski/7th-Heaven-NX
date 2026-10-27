"""Port one ESUI-patched x86 function onto the Switch ARM body.

Per function config (FNS):
  struct_x86 : [(va, len)]   x86 byte ranges that change control flow; they are
                             taken from the ESUI exe into the 'struct-only' x86
  arm_struct : {va: word}    the matching ARM word edits
  skip_x86   : [(va, len)]   ESUI bytes NOT emulated here (handled elsewhere);
                             the stock bytes stay in the ESUI exe for this fn
Then every remaining ESUI change must be a change of call ARGUMENTS; those are
fitted per ARM callsite (cs_fit) and carried by a callsite cave each.

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
import sys, os, struct, difflib, collections, json
from fractions import Fraction
SC = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, '.'); sys.path.insert(0, 'tools/esui'); sys.path.insert(0, SC)
import cemu as D, newexe, exe_patch, nxmap
import a64 as A
EXE = open('dump/romfs/ff7/resources/ff7_1.02/ff7_en', 'rb').read()
PE = exe_patch.parse_pe(EXE)
OFF = lambda va: exe_patch.va_to_offset(PE, va)
NEW_FULL = newexe.build()
M = D.M
A2X = {v: k for k, v in M.x86_to_arm.items()}
TRANSLATE = 0x10FC3A0
CTX_PTR = 0x12CE2B0
NOP = 0xD503201F
ARGSETS = [[0,0,0,0],[1,2,3,0],[2,0,1,1],[3,1,0,2],[0xff,0xff,0,0],[4,4,4,4],[1,1,1,1],[2,2,2,2]]

def s32(v):
    v &= 0xffffffff
    return v - (1 << 32) if v & 0x80000000 else v

def dyn(cfg, xn, pk):
    """ESUI code bytes written at run time by a cave elsewhere: apply them
    to the ESUI exe for this run, from the run's pokes."""
    d = cfg.get('x86_dyn')
    if not d: return xn
    b = bytearray(xn)
    for cond, val, code, yes, no in d:
        b[OFF(code)] = yes if ((pk or {}).get(cond, 0) & 0xff) == val else no
    return bytes(b)

TEXT_LO, TEXT_HI = 0x401000, 0x7B562B
_ARITY = {0x6f54a2: 1, 0x41963c: 3, 0x6f638c: 2 + 12, 0x6e7d20: 2 + 4}   # text width (1 arg), text fetch (3): the scan over-reads them

def arity(x86fn):
    """how many dword args the x86 function reads ([ebp+8..]); 8 if unknown"""
    if x86fn in _ARITY: return _ARITY[x86fn]
    import capstone, re
    n = 8
    try:
        fs, fe = M.containing(x86fn)
        cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        mx = 0
        for i in cs.disasm(EXE[OFF(fs):OFF(fe)], fs):
            for m in re.finditer(r'ebp \+ (0x[0-9a-f]+|\d+)\]', i.op_str):
                mx = max(mx, int(m.group(1), 0))
        n = max(0, (mx - 8) // 4 + 1) if mx >= 8 else 0
    except Exception:
        n = 8
    _ARITY[x86fn] = min(n, 8)
    return _ARITY[x86fn]

def _data_new():
    """stock .text + every ESUI byte outside .text (data patches and ESUI's
    own caves): the guest image both sides run on."""
    b = bytearray(EXE)
    for name, va, vs, ro, rs in PE['sections']:
        lo, hi = ro, ro + rs
        if va <= TEXT_LO < va + vs:
            continue
        b[lo:hi] = NEW_FULL[lo:hi]
    return bytes(b)
# x86 .text bytes the port reads as DATA (its jump tables are compare chains
# over the x86 tables): ESUI's changes there are guest-image patches too
GUEST_TEXT = {0x6D7BB3: 0x10}     # battle sub-menu dispatcher: case 21 (status help) -> none
def _with_guest_text(buf):
    b = bytearray(buf)
    for va, v in GUEST_TEXT.items():
        b[OFF(va)] = v
    return bytes(b)
DATA_NEW = _with_guest_text(_data_new())

def exes(cfg):
    st = bytearray(DATA_NEW); nw = bytearray(NEW_FULL)
    for va, n in cfg.get('struct_x86', []):
        o = OFF(va); st[o:o + n] = NEW_FULL[o:o + n]
    for va, n in cfg.get('skip_x86', []):
        o = OFF(va); nw[o:o + n] = EXE[o:o + n]; st[o:o + n] = EXE[o:o + n]
    return bytes(st), bytes(nw)

SNOISE = set()     # (ARM call site, arg): the port's own value differs from the x86's, ESUI leaves it
WPAIRS = collections.defaultdict(list); SJW = collections.defaultdict(set); W2SJ = collections.defaultdict(set)

def sample(fn, cfg, seeds=range(6), argsets=ARGSETS, pokes=(None,)):
    WPAIRS.clear(); SJW.clear(); W2SJ.clear()
    xs, xn = exes(cfg)
    D.NATIVE = set(cfg.get('native', ())) | set(cfg.get('native_both', ()))
    D.NATIVE_ARM = set(cfg.get('native_both', ()))
    pairs = collections.defaultdict(lambda: collections.defaultdict(list))
    tgt = {}; stats = collections.Counter()
    for seed in seeds:
        D.RETMODE[0] = seed
        for args in argsets:
            for pk in pokes:
                D.POKES = pk or {}
                c0, w0, s0 = D.run_x86(xs, fn, seed, args)
                c1, w1, s1 = D.run_x86(dyn(cfg, xn, None), fn, seed, args)
                d0, v0, r0 = D.run_arm(cfg.get('arm_struct', {}), DATA_NEW, fn, seed, args)
                sites = list(D.LAST_SITES); writers = list(D.LAST_WRITERS)
                if (s0, s1, r0) != ('ok', 'ok', 'ok'):
                    stats['notok'] += 1; continue
                if [t for t, _ in c0] != [t for t, _ in c1]:
                    stats['struct-only vs esui callseq differ'] += 1; continue
                if [t for t, _ in c0] != [t for t, _ in d0]:
                    stats['arm(struct) vs x86(struct) seq differ'] += 1
                sm = difflib.SequenceMatcher(None, [t for t, _ in c0], [t for t, _ in d0], autojunk=False)
                for tg, i1, i2, j1, j2 in sm.get_opcodes():
                    if tg != 'equal': continue
                    for d in range(i2 - i1):
                        k, q = i1 + d, j1 + d
                        tgt[sites[q]] = c0[k][0]
                        for j in range(arity(c0[k][0])):
                            smp = (s32(c0[k][1][j]), s32(c1[k][1][j]), s32(d0[q][1][j]))
                            pairs[sites[q]][j].append(smp)
                            if smp[0] != smp[2] and smp[0] == smp[1]: SNOISE.add((sites[q], j))
                            wpc = writers[q][j] if j < len(writers[q]) else None
                            WPAIRS[wpc].append(smp)
                            SJW[(sites[q], j)].add(wpc); W2SJ[wpc].add((sites[q], j))
                # memory: per ARM store, the value it left vs what the x86 left there
                for g, (pc, sz, val) in list(D.LAST_MEMW.items()):
                    if all(g + k in w0 and g + k in w1 for k in range(sz)) and pc_in_fn(pc, fn):
                        o = sum(w0[g + k] << (8 * k) for k in range(sz)); n = sum(w1[g + k] << (8 * k) for k in range(sz))
                        WPAIRS[('m', pc)].append((o, n, val))
                stats['runs'] += 1
    return pairs, tgt, stats

def pc_in_fn(pc, fn):
    a0, a1 = M.extent(M.containing(fn)[0])
    return a0 <= pc < a1

def fit(samples):
    if all(o == n for o, n, a in samples): return 'same'
    ds = {n - o for o, n, a in samples}
    # a constant shift applies to the port's own value even where the port
    # computes it slightly differently from the x86 (e.g. its own text widths)
    if len(ds) == 1: return ('add', ds.pop())
    # ESUI's 'add al, k': the low byte wraps on its own
    for d in range(-255, 256):
        if d and all(n == (o & ~0xff) | ((o + d) & 0xff) for o, n, a in samples):
            return ('addb', d & 0xff)
    news = {n for o, n, a in samples}
    if len(news) == 1 and len({o for o, n, a in samples}) > 1:
        return ('const', news.pop())
    if any(o != a for o, n, a in samples): return None
    news = {n for o, n, a in samples}
    if len(news) == 1: return ('const', news.pop())
    pts = sorted({(o, n) for o, n, a in samples})
    (o1, n1), (o2, n2) = pts[0], pts[-1]
    if o2 == o1: return None
    sl = Fraction(n2 - n1, o2 - o1)
    if sl.denominator > 1024: return None
    if sl.denominator & (sl.denominator - 1) and len(pts) < 3: return None   # two points fit anything
    if any(Fraction(n - n1) != sl * (o - o1) for o, n in pts): return None
    if sl.denominator & (sl.denominator - 1):
        return ('affd', sl.numerator, sl.denominator, o1, n1)
    return ('aff', sl.numerator, sl.denominator.bit_length() - 1, o1, n1)

def fit_all(pairs, tgt):
    out = {}; bad = {}
    for site in sorted(pairs):
        tr = {}
        for j, sm in pairs[site].items():
            f = fit(sm)
            if f == 'same': continue
            if f is None: bad.setdefault(site, []).append((j, sm[:6]))
            else: tr[j] = f
        if tr: out[site] = (tgt[site], tr)
    return out, bad

def _is_push_store(pc):
    w = struct.unpack_from('<I', M.img, pc)[0] if pc is not None else 0
    return (w & 0xFFC00000) in STORE_SIZES

def fit_all2(pairs, tgt):
    """fit_all, then: an argument no single callsite transform fits (the
    port merged several x86 call sites into one ARM call, e.g. per-language
    table loads) is fitted per ARM STORE that wrote it instead.  Returns
    (sites, bad, stores {store pc: transform})."""
    out, bad = fit_all(pairs, tgt)
    mem = {}
    for key, smp in WPAIRS.items():
        if isinstance(key, tuple) and key[0] == 'm' and any(o != n for o, n, a in smp):
            f = fit(smp) or fit_map(smp)
            if f is None: bad.setdefault(('mem', key[1]), []).append((0, smp[:4]))
            elif f != 'same': mem[key[1]] = f
    todo = {(s, j) for s, v in bad.items() for j, _ in v if not isinstance(s, tuple)}
    if not todo:
        return out, bad, mem
    ws = set()
    while True:
        n = (len(todo), len(ws))
        for sj in list(todo): ws |= SJW[sj]
        for w in list(ws): todo |= W2SJ[w]
        if (len(todo), len(ws)) == n: break
    stores = {}; left = {}
    for w in sorted(ws, key=lambda x: x or 0):
        f = fit(WPAIRS[w]) if _is_push_store(w) else None
        if f is None and _is_push_store(w):
            f = fit_map(WPAIRS[w])
        if f is None:
            for (s, j) in W2SJ[w]: left.setdefault(s, []).append((j, ('store', hex(w) if w else w, WPAIRS[w][:4])))
        elif f != 'same':
            stores[w] = f
    for (s, j) in todo:
        if s in out:
            out[s][1].pop(j, None)
            if not out[s][1]: del out[s]
    left.update({k: v for k, v in bad.items() if isinstance(k, tuple)})
    stores.update(mem)
    return out, left, stores

def fit_map(samples):
    """the store is reached from several x86 sites through one merged ARM
    path: value -> value, keyed on what the port stores"""
    m = {}
    for o, n, a in samples:
        if m.setdefault(a, n) != n: return None
    # only a handful of table constants; a spread of values is a formula the
    # map would get wrong for anything unseen
    if len(m) > 6: return None
    return ('map', tuple(sorted((a, n) for a, n in m.items() if a != n)))

SDIV = lambda rd, rn, rm: 0x1AC00C00 | (rm << 16) | (rn << 5) | rd
STORE_SIZES = {0xB9000000: 4, 0x79000000: 2, 0x39000000: 1}

def fit_cfg(cfg, pairs, tgt):
    """fit_all2, then the config's own site transforms (site_tr: {site: {j: tr}})
    win over (and excuse) whatever was fitted for those args."""
    sites, bad, stores = fit_all2(pairs, tgt)
    for pc_, tr in cfg.get('store_tr', {}).items():
        stores[pc_] = tr
        for k in [k for k, v in bad.items() if any(isinstance(d, tuple) and len(d) > 1 and d[1] == hex(pc_) for j, d in v)]:
            del bad[k]
    for site, tr in cfg.get('site_tr', {}).items():
        callee = sites[site][0] if site in sites else A2X[_bl_target(site)]
        cur = dict(sites[site][1]) if site in sites else {}
        cur.update(tr)
        sites[site] = (callee, cur)
        if site in bad:
            bad[site] = [(j, d) for j, d in bad[site] if j not in tr]
            if not bad[site]: del bad[site]
    return sites, bad, stores

def _bl_target(site):
    w = struct.unpack_from('<I', M.img, site)[0]
    off = w & 0x3FFFFFF
    if off & 0x2000000: off -= 0x4000000
    return site + 4 * off

def store_words(at, site, f):
    """the shipped store cave (ff7nx_esuicave.store_cave)"""
    import ff7nx_esuicave as EC
    old = struct.unpack_from('<I', M.img, site)[0]
    return EC.store_cave(at, site, old, tuple(f), M.x86_to_arm)

# ------------------------------------------------------------- cave words
def mov32(rd, v):
    v &= 0xffffffff
    w = [A.movz(rd, v & 0xffff)]
    if v >> 16: w.append(A.movk_hi(rd, v >> 16))
    return w

def cave_words(at, site, callee_arm, tr, post=()):
    """Words of a callsite cave starting at `at`; `at` is where word 0 lands
    (the caves here are contiguous in the dead body)."""
    w = []
    pc = lambda: at + 4 * len(w)
    w.append(A.stp64_pre(29, 30, 31, -16))              # stp x29, x30, [sp, #-16]!
    for j, f in sorted(tr.items()):
        w.append(A.adrp(0, pc(), CTX_PTR))
        w.append(A.ldr64(0, 0, CTX_PTR & 0xfff))
        w.append(A.ldr(0, 0, 0x10))                     # w0 = guest esp
        w.append(A.add_imm(0, 0, 4 + 4 * j))
        w.append(A.bl(pc(), TRANSLATE))                 # x0 = host ptr; x8-x10 clobbered
        if f[0] == 'const':
            w += mov32(8, f[1])
        elif f[0] == 'add':
            w.append(A.ldr(8, 0, 0))
            d = f[1]
            if 0 <= d < 4096: w.append(A.add_imm(8, 8, d))
            elif -4096 < d < 0: w.append(A.sub_imm(8, 8, -d))
            else: w += mov32(9, d); w.append(A.add_reg(8, 8, 9))
        elif f[0] == 'addb':
            w.append(A.ldr(8, 0, 0))
            w.append(A.add_imm(9, 8, f[1])); w.append(A.bfi(8, 9, 0, 8))
        elif f[0] == 'affd':
            _, num, den, o1, n1 = f
            w.append(A.ldr(8, 0, 0))
            w += mov32(9, o1); w.append(A.sub_reg(8, 8, 9))
            w += mov32(9, num); w.append(A.mul(8, 8, 9))
            w += mov32(9, den); w.append(SDIV(8, 8, 9))
            w += mov32(9, n1); w.append(A.add_reg(8, 8, 9))
        else:
            _, num, sh, o1, n1 = f
            w.append(A.ldr(8, 0, 0))
            w += mov32(9, o1); w.append(A.sub_reg(8, 8, 9))
            if num != 1: w += mov32(9, num); w.append(A.mul(8, 8, 9))
            if sh: w.append(A.asr(8, 8, sh))
            w += mov32(9, n1); w.append(A.add_reg(8, 8, 9))
        w.append(A.str_(8, 0, 0))
    # post ops: arg j += add when guest byte [addr] == val (ESUI writes a
    # code byte at run time for this; the port has no code bytes to write)
    for j, addr, val, add in post:
        w += mov32(0, addr)
        w.append(A.bl(pc(), TRANSLATE))
        w.append(A.ldrb(9, 0, 0))
        w.append(A.cmp_imm(9, val))
        skip_at = len(w); w.append(0)
        w.append(A.adrp(0, pc(), CTX_PTR))
        w.append(A.ldr64(0, 0, CTX_PTR & 0xfff))
        w.append(A.ldr(0, 0, 0x10))
        w.append(A.add_imm(0, 0, 4 + 4 * j))
        w.append(A.bl(pc(), TRANSLATE))
        w.append(A.ldr(8, 0, 0))
        w += mov32(9, add); w.append(A.add_reg(8, 8, 9))
        w.append(A.str_(8, 0, 0))
        w[skip_at] = A.bcond(at + 4 * skip_at, pc(), 1)     # b.ne past the block
    w.append(A.ldp64_post(29, 30, 31, 16))
    w.append(A.b(pc(), callee_arm))
    return w

def build_caves(sites, base, posts=None):
    """{va: word} for all callsite caves laid out from `base`, plus the bl
    rewrites at the sites."""
    out = {}; at = base
    for site, (callee_x86, tr) in sorted(sites.items()):
        callee = M.x86_to_arm[callee_x86]
        old = struct.unpack_from('<I', M.img, site)[0]
        assert (old & 0xFC000000) == 0x94000000, hex(site)
        import ff7nx_esuicave as EC
        ws = EC.callsite_cave(at, callee, tr, (posts or {}).get(site, ()), M.x86_to_arm)
        for k, x in enumerate(ws): out[at + 4 * k] = x
        out[site] = A.bl(site, at)
        at += 4 * len(ws)
    return out, at

def verify(fn, cfg, edits, seeds=range(6), argsets=ARGSETS, ignore=()):
    xs, xn = exes(cfg)
    xs = EXE
    bad = tot = 0; first = None
    for seed in seeds:
        D.RETMODE[0] = seed
        for args in argsets:
            c0, w0, s0 = D.run_x86(EXE, fn, seed, args)
            c1, w1, s1 = D.run_x86(xn, fn, seed, args)
            d0, v0, r0 = D.run_arm({}, EXE, fn, seed, args)
            d1, v1, r1 = D.run_arm(edits, EXE, fn, seed, args)
            if (s0, s1, r0, r1) != ('ok',) * 4:
                bad += 1; first = first or ('status', seed, args, (s0, s1, r0, r1)); continue
            dx = D.diff(c0, w0, c1, w1); da = D.diff(d0, v0, d1, v1)
            dx = [e for e in dx if not (e[0] == 'mem' and e[1] in ignore)]
            da = [e for e in da if not (e[0] == 'mem' and e[1] in ignore)]
            # calls compare by index; x86 and ARM stock call lists must align 1:1
            same_seq = [t for t, _ in c1] == [t for t, _ in d1]
            tot += 1
            if not same_seq or set(map(str, dx)) != set(map(str, da)):
                bad += 1
                if first is None:
                    sx = set(map(str, dx)); sa = set(map(str, da))
                    first = (seed, args, same_seq, sorted(sx - sa)[:8], sorted(sa - sx)[:8])
    return bad, tot, first

def verify2(fn, cfg, edits, seeds=range(6), argsets=ARGSETS, pokes=(None,), ignore_mem=(), watch=(), verbose=4):
    """x86 ESUI vs ARM edited, call by call (aligned on callee), every arg
    that matches between x86 stock and ARM stock must match after; memory:
    the change sets (stock->ESUI vs stock->edited) must be equal."""
    xs, xn = exes(cfg)
    ignore_mem = tuple(ignore_mem) + tuple(cfg.get('ignore_mem', ()))
    D.NATIVE = set(cfg.get('native', ())) | set(cfg.get('native_both', ()))
    D.NATIVE_ARM = set(cfg.get('native_both', ()))
    D.WATCH = set(watch)
    cov = set(); acov = set()
    bad = tot = 0; shown = 0
    for seed in seeds:
        D.RETMODE[0] = seed
        for args in argsets:
            for pk in pokes:
                D.POKES = pk or {}
                c0, w0, s0 = D.run_x86(DATA_NEW, fn, seed, args)
                c1, w1, s1 = D.run_x86(dyn(cfg, xn, pk), fn, seed, args); cov |= D.LAST_COV
                d0, v0, r0 = D.run_arm({}, DATA_NEW, fn, seed, args); sa0 = list(D.LAST_SITES)
                d1, v1, r1 = D.run_arm(edits, DATA_NEW, fn, seed, args); acov |= D.LAST_ACOV; sa1 = list(D.LAST_SITES)
                if s0 != 'ok' and s1 != 'ok':
                    continue            # the x86 itself faults on this random state
                tot += 1
                probs = []
                if (s0, s1, r0, r1) != ('ok',) * 4:
                    probs.append(('status', s0, s1, r0, r1))
                else:
                    # stock alignment x86<->ARM gives which arg positions are comparable
                    def align(a, b):
                        sm = difflib.SequenceMatcher(None, [t for t, _ in a], [t for t, _ in b], autojunk=False)
                        m = {}
                        for tg, i1, i2, j1, j2 in sm.get_opcodes():
                            if tg == 'equal':
                                for d in range(i2 - i1): m[i1 + d] = j1 + d
                        return m
                    m1 = align(c1, d1)
                    tx1 = [t for t, _ in c1]; ta1 = [t for t, _ in d1]
                    # every x86 call must be present on ARM, except callees the ARM inlines
                    inl = set(tx1) - set(ta1)
                    stock_same = [t for t, _ in c0] == [t for t, _ in d0]
                    if stock_same and [t for t in tx1 if t not in inl] != [t for t in ta1 if t in set(tx1)]:
                        probs.append(('callseq', len(c1), len(d1)))
                    if not stock_same and ([t for t, _ in c1] != [t for t, _ in c0] or [t for t, _ in d1] != [t for t, _ in d0]):
                        # the port draws differently here already; only ESUI's own changes must agree
                        if ([t for t, _ in c1] == [t for t, _ in c0]) != ([t for t, _ in d1] == [t for t, _ in d0]):
                            probs.append(('callseq-delta', len(c1), len(d1)))
                    for k, q in m1.items():
                        for j in range(arity(c1[k][0])):
                            if c1[k][1][j] != d1[q][1][j]:
                                probs.append(('arg', k, hex(c1[k][0]), j, hex(c1[k][1][j]), hex(d1[q][1][j]), sa1[q]))
                    mx = {a for a in set(w0) | set(w1) if w0.get(a) != w1.get(a)} - set(ignore_mem)
                    ma = {a for a in set(v0) | set(v1) if v0.get(a) != v1.get(a)} - set(ignore_mem)
                    # the guest stack: only slots the port writes too (an inlined callee's
                    # pushes exist on the x86 side alone)
                    _stk = lambda a: 0x0FEF0000 <= a < 0x0FF01000
                    mx = {a for a in mx if not (_stk(a) and a not in v0 and a not in v1)}
                    for a in sorted(mx ^ ma)[:6]:
                        probs.append(('mem', hex(a) if a >= 0 else a, w1.get(a), v1.get(a)))
                    # ... and where both changed, to the same value (where stock agreed)
                    for a in sorted(mx & ma):
                        if a >= 0 and w0.get(a) == v0.get(a) and w1.get(a) != v1.get(a):
                            probs.append(('memval', hex(a), w1.get(a), v1.get(a)))
                            break
                    # arg probs where the STOCK pair already differed are port noise: drop
                    m0 = align(c0, d0)
                    noise = set()
                    for k, q in m0.items():
                        for j in range(min(len(c0[k][1]), len(d0[q][1]))):
                            if c0[k][1][j] != d0[q][1][j]: noise.add((k, j))
                    # ... and by ARM call site, where the port's own call sequence shifts the indices
                    SNOISE.update((sa0[q], j) for k, q in m0.items() for j in range(min(len(c0[k][1]), len(d0[q][1]))) if c0[k][1][j] != d0[q][1][j])
                    snoise = SNOISE
                    probs = [p[:6] for p in probs if not (p[0] == 'arg' and ((p[1], p[3]) in noise or (p[6], p[3]) in snoise))] + \
                            [p for p in probs if p[0] != 'arg']
                if probs:
                    bad += 1
                    if shown < verbose:
                        shown += 1
                        print('  seed %d args %s pk %s:' % (seed, args, bool(pk)))
                        for p in probs[:10]: print('     ', p)
    miss = sorted(set(watch) - cov)
    print('fn %x: %d/%d runs differ; watched x86 covered %d/%d%s' % (fn, bad, tot, len(set(watch)) - len(miss), len(set(watch)),
          ('  MISSING ' + ' '.join('%x' % a for a in miss)) if miss else ''))
    ne = sorted(a for a in edits if a in set(cfg.get('arm_struct', {})) and a not in acov)
    if ne: print('   struct edits never executed: ' + ' '.join('%x' % a for a in ne))
    return bad

def data_refs(fn):
    """(absolute data addresses the x86 function reads, constants it compares
    against).  Addresses exact (byte reads may be unaligned)."""
    import capstone
    from capstone import x86 as X
    cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32); cs.detail = True
    fs, fe = M.containing(fn)
    out = set(); consts = set()
    for i in cs.disasm(EXE[OFF(fs):OFF(fe)], fs):
        for op in i.operands:
            if op.type == X.X86_OP_MEM and 0x800000 <= (op.mem.disp & 0xffffffff) < 0x2000000:
                out.add(op.mem.disp & 0xffffffff)
        if i.mnemonic == 'cmp' and i.operands[-1].type == X.X86_OP_IMM:
            consts.add(i.operands[-1].imm & 0xffffffff)
    return sorted(out), sorted(consts)

def random_pokes(fn, n=40, seed=1, extra=()):
    import random
    rng = random.Random(seed)
    refs, consts = data_refs(fn)
    refs = sorted(set(refs) | set(extra))
    vals = [0, 1, 2, 3, 4, 0xff, 0xffff, 0x80, 0x100] + list(consts)
    out = [None]
    for _ in range(n):
        out.append({a: (rng.choice(vals) if rng.random() < 0.8 else rng.randrange(0, 0x10000))
                    for a in refs if rng.random() < 0.5})
    return out

PARTY = {0xdc0230: 0xff020100, 0xbfb2a8: 0x00ffffff, 0xbfb2ac: 0x00ffffff, 0xbfb2b0: 0x00ffffff}
def party_pokes():
    out = []
    for extra in ({}, {0xdc2088: 0}, {0xdc2088: 1}, {0xdc2088: 2}, {0xdc1f44: 0x10}, {0xdc1f44: 0x23},
                  {0xdc2069: 2, 0xdc3c7c: 1, 0xdc2080: 0}, {0xdc2069: 2, 0xdc3c7c: 2, 0xdc2080: 0},
                  {0x9ad1d0: 1}, {0xdba4b0: 0xffff, 0xdba8f0: 0xffff, 0xdbad30: 0xffff}):
        d = dict(PARTY); d.update(extra); out.append(d)
    # no battle sub-window open (every 6D0AE8 slot 0), each actor active
    calm = {0xdc2068 + 4 * k: 0 for k in range(9)}
    for actor in (0, 1, 2):
        d = dict(PARTY); d.update(calm); d[0xdc3c7c] = actor
        d[0xdba498 + actor * 0x440 + 0x21] = 3      # command columns of the actor
        out.append(d)
        d = dict(d); d[0xdc2088] = 2; out.append(d)
    return out

def auto_native(fn):
    """x86 callees of `fn` the port inlined (no bl to them in its ARM body):
    run them natively on the x86 side so both read the same memory."""
    import capstone
    cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    fs, fe = M.containing(fn)
    xc = set()
    for i in cs.disasm(EXE[OFF(fs):OFF(fe)], fs):
        if i.mnemonic == 'call' and i.op_str.startswith('0x'):
            xc.add(int(i.op_str, 16))
    a0, a1 = M.extent(fs)
    ac = set()
    for a in range(a0, a1, 4):
        w = struct.unpack_from('<I', M.img, a)[0]
        if (w & 0xFC000000) == 0x94000000:
            off = w & 0x3FFFFFF
            if off & 0x2000000: off -= 0x4000000
            t = a + off * 4
            if t in A2X: ac.add(A2X[t])
            elif t != TRANSLATE and t > a1:
                # a native wrapper the port calls instead: whatever recompiled
                # body it branches on to is still a call, not an inlining
                for k in range(0, 0x200, 4):
                    v = struct.unpack_from('<I', M.img, t + k)[0]
                    if (v & 0x7C000000) == 0x14000000:
                        o2 = v & 0x3FFFFFF
                        if o2 & 0x2000000: o2 -= 0x4000000
                        if t + k + o2 * 4 in A2X: ac.add(A2X[t + k + o2 * 4])
                    if v == 0xD65F03C0 and k > 0x100: break
    return sorted(c for c in xc - ac if c in M.x86_to_arm and c < 0x7B562B)

def _imms(text):
    import re
    return {abs(int(x, 16)) for x in re.findall(r'-?0x[0-9a-f]+', text)} | \
           {abs(int(x)) for x in re.findall(r'(?<![0-9a-fx])-?\b[0-9]+\b(?![x0-9])', text.split(',', 1)[-1])}

def _same_imm(i, j, o, n):
    xo, xn = _imms(i.op_str), _imms(j.op_str)
    ao, an = _imms(o.split(' ', 1)[-1].split('#', 1)[-1]) if '#' in o else set(), \
             _imms(n.split(' ', 1)[-1].split('#', 1)[-1]) if '#' in n else set()
    return bool(ao & (xo - xn)) and bool(an & (xn - xo))

def align_explicit(fn, statuses=('OK', 'GROUP', 'DEF', 'REGSWAP')):
    """align.py's in-place word edits for this function, minus pushes (the
    callsite caves own those): (x86 ranges, {arm va: word})."""
    import io, contextlib, capstone
    import align as AL
    AL.build_new = lambda pre: NEW_FULL
    AL.EDITS = []; AL.STRUCTS = []; AL.GROUPS = {}
    sys.argv = ['align', hex(fn)]
    with contextlib.redirect_stdout(io.StringIO()):
        AL.main(); AL.resolve_groups()
    cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    rngs = []; words = {}
    for st, xva, a, w0, w1, o, n in AL.EDITS:
        base = st.split('(')[0].split('?')[0].split('+')[0]
        if base not in statuses or w1 is None or '?' in st:
            continue
        i = next(cs.disasm(EXE[OFF(xva):OFF(xva) + 16], xva))
        if i.mnemonic == 'push':
            continue
        # align also accepts near matches ('#0x40' for an x86 0x3f): only an
        # exact immediate pair is an edit of the same constant
        j = next(cs.disasm(NEW_FULL[OFF(xva):OFF(xva) + 16], xva))
        if not _same_imm(i, j, o, n):
            continue
        if a in words and words[a] != w1:
            continue
        rngs.append((xva, i.size)); words[a] = w1
    return sorted(set(rngs)), words

def patched(va, n):
    o = OFF(va)
    return NEW_FULL[o:o + n] != EXE[o:o + n]

def cfg_for(fn, manual=None):
    import fns
    cfg = dict((manual if manual is not None else fns.FNS).get(fn, {}))
    # BUILD 629 option variants: hand edits that belong to one option folder's
    # x86 patch apply only where this variant has that patch
    for arm_va, (xva, n) in cfg.get('gate', {}).items():
        if not patched(xva, n):
            cfg['arm_struct'] = {k: v for k, v in cfg.get('arm_struct', {}).items() if k != arm_va}
            cfg['replace'] = [r for r in cfg.get('replace', []) if r[0] != arm_va]
            cfg['hooks'] = [h for h in cfg.get('hooks', []) if h[0] != arm_va]
            cfg['struct_x86'] = [r for r in cfg.get('struct_x86', []) if r[0] != xva]
    if not cfg.get('no_align'):
        r, w = align_explicit(fn)
        man = cfg.get('struct_x86', [])
        def hit(a, n): return any(not (a + n <= b or b + m <= a) for b, m in man)
        r = [(a, n) for a, n in r if not hit(a, n)]
        words = dict(w); words.update(cfg.get('arm_struct', {}))
        cfg['struct_x86'] = list(man) + r
        cfg['arm_struct'] = words
    if 'native' not in cfg:
        # BUILD 628: a callee the port DOES call (the scan missed its bl, e.g.
        # behind a wrapper) must not run natively on the x86 side, or its
        # calls -- the menus' text draws -- are never compared
        nat = auto_native(fn)
        if nat:
            D.NATIVE = set(); D.NATIVE_ARM = set(); D.POKES = {}
            seen = set()
            for args in ARGSETS[:2]:
                d, _, _ = D.run_arm({}, DATA_NEW, fn, 0, args)
                seen |= {t for t, _ in d}
            nat = [n for n in nat if n not in seen]
        cfg['native'] = nat
    if cfg.get('native_extra'):
        cfg['native'] = list(cfg['native']) + [n for n in cfg['native_extra'] if n not in cfg['native']]
    return cfg

def hook_words(at, site, ops):
    """Side-effect hook (the shipped builder's, ff7nx_esuicave.hook_cave)."""
    import ff7nx_esuicave as EC
    displaced = struct.unpack_from('<I', M.img, site)[0]
    if EC._pc_relative(displaced):
        raise ValueError('pc-relative word at %x' % site)
    return EC.hook_cave(at, site, displaced, ops)

def build_all(cfg, sites, base, stores=None):
    edits = dict(cfg.get('arm_struct', {}))
    caves, at = build_caves(sites, base, cfg.get('post'))
    edits.update(caves)
    for site, f in sorted((stores or {}).items()):
        ws = store_words(at, site, f)
        for k, x in enumerate(ws): edits[at + 4 * k] = x
        edits[site] = A.b(site, at)
        at += 4 * len(ws)
    for site, words in cfg.get('replace', []):
        import ff7nx_esuicave as EC
        ws = EC.replace_words(at, site, words)
        for k, x in enumerate(ws): edits[at + 4 * k] = x
        edits[site] = A.b(site, at)
        at += 4 * len(ws)
    for site in cfg.get('skip_calls', []):
        # ESUI nops a call: the port's callee pops its return slot, so a
        # skipped call must pop it here; eax stays as it was, as on x86
        old = struct.unpack_from('<I', M.img, site)[0]
        assert (old & 0xFC000000) == 0x94000000, hex(site)
        ws = [A.stp64_pre(9, 10, 31, -16), A.adrp(9, at + 4, CTX_PTR), A.ldr64(9, 9, CTX_PTR & 0xfff),
              A.ldr(10, 9, 0x10), A.add_imm(10, 10, 4), A.str_(10, 9, 0x10), A.ldp64_post(9, 10, 31, 16)]
        ws.append(A.b(at + 4 * len(ws), site + 4))
        for k, x in enumerate(ws): edits[at + 4 * k] = x
        edits[site] = A.b(site, at)
        at += 4 * len(ws)
    for site, val in cfg.get('store_const', []):
        # the site is 'str wN, [x0]' whose wN is a shared constant; store val
        old = struct.unpack_from('<I', M.img, site)[0]
        assert (old & 0xFFFFFFE0) == 0xB9000000 | 0 and ((old >> 5) & 31) == 0, hex(site)
        ws = [A.stp64_pre(9, 10, 31, -16)] + mov32(9, val) + [A.str_(9, 0, 0), A.ldp64_post(9, 10, 31, 16)]
        ws.append(A.b(at + 4 * len(ws), site + 4))
        for k, x in enumerate(ws): edits[at + 4 * k] = x
        edits[site] = A.b(site, at)
        at += 4 * len(ws)
    for site, ops in cfg.get('hooks', []):
        ws = hook_words(at, site, ops)
        for k, x in enumerate(ws): edits[at + 4 * k] = x
        edits[site] = A.b(site, at)
        at += 4 * len(ws)
    return edits, at
