"""Fit per-ARM-callsite argument transforms that reproduce ESUI's x86 change.

For x86 function F: run stock x86, ESUI x86 and the port's stock ARM body over
several seeds/argsets; align call k (x86) with call k (ARM) by callee; record
at each ARM callsite the (stock arg, ESUI arg) pairs; fit new = f(old).

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
import sys, os, difflib, collections, json
from fractions import Fraction
sys.path.insert(0, '.'); sys.path.insert(0, 'tools/esui'); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cemu as D, newexe
EXE = open('dump/romfs/ff7/resources/ff7_1.02/ff7_en', 'rb').read()
NEW = newexe.build()
ARGSETS = [[0,0,0,0],[1,2,3,0],[2,0,1,1],[3,1,0,2],[0xff,0xff,0,0],[4,4,4,4],[1,1,1,1],[2,2,2,2]]
def s32(v):
    v &= 0xffffffff
    return v - (1 << 32) if v & 0x80000000 else v
def sample(fn, seeds=range(6), argsets=ARGSETS, pokes=(None,)):
    pairs = collections.defaultdict(lambda: collections.defaultdict(list))
    tgt = {}
    stats = collections.Counter()
    for seed in seeds:
        D.RETMODE[0] = seed
        for args in argsets:
          for pk in pokes:
            D.POKES = pk or {}
            c0, w0, s0 = D.run_x86(EXE, fn, seed, args)
            c1, w1, s1 = D.run_x86(NEW, fn, seed, args)
            d0, v0, r0 = D.run_arm({}, EXE, fn, seed, args)
            sites = list(D.LAST_SITES)
            if 'ok' != s0 or 'ok' != s1 or r0 != 'ok':
                stats['notok'] += 1; continue
            if [t for t, _ in c0] != [t for t, _ in c1]:
                stats['x86 callseq changed'] += 1; continue
            if [t for t, _ in c0] != [t for t, _ in d0]:
                stats['arm/x86 seq differ'] += 1
            sm = difflib.SequenceMatcher(None, [t for t, _ in c0], [t for t, _ in d0], autojunk=False)
            for tg, i1, i2, j1, j2 in sm.get_opcodes():
                if tg != 'equal': continue
                for d in range(i2 - i1):
                    k, q = i1 + d, j1 + d
                    site = sites[q]
                    tgt[site] = c0[k][0]
                    for j in range(8):
                        pairs[site][j].append((s32(c0[k][1][j]), s32(c1[k][1][j]), s32(d0[q][1][j])))
            stats['runs'] += 1
    return pairs, tgt, stats
def fit(samples):
    """samples: [(x86 old, x86 new, arm old)].  Returns ('const',v) / ('add',d) /
    ('aff', num, shift, b_old, b_new) / None.  Transform maps ARM old -> new."""
    if all(o == n for o, n, a in samples): return 'same'
    if any(o != a for o, n, a in samples):
        return None          # ARM stock arg differs from x86 stock: cannot map
    ds = {n - o for o, n, a in samples}
    if len(ds) == 1: return ('add', ds.pop())
    news = {n for o, n, a in samples}
    olds = sorted({o for o, n, a in samples})
    if len(news) == 1 and len(olds) > 1: return ('const', news.pop())
    # affine with power-of-two denominator slope
    pts = sorted({(o, n) for o, n, a in samples})
    (o1, n1), (o2, n2) = pts[0], pts[-1]
    if o2 == o1: return None
    sl = Fraction(n2 - n1, o2 - o1)
    if sl.denominator & (sl.denominator - 1): return None
    if any(Fraction(n - n1) != sl * (o - o1) for o, n in pts): return None
    return ('aff', sl.numerator, sl.denominator.bit_length() - 1, o1, n1)
if __name__ == '__main__':
    fn = int(sys.argv[1], 16)
    pairs, tgt, stats = sample(fn)
    print('fn %x %s' % (fn, dict(stats)))
    out = {}
    for site in sorted(pairs):
        tr = {}
        bad = []
        for j, sm in pairs[site].items():
            f = fit(sm)
            if f == 'same': continue
            if f is None: bad.append((j, sm[:4]))
            else: tr[j] = f
        if tr or bad:
            print('  site %x -> %x  %s %s' % (site, tgt[site], tr, ('BAD %s' % bad) if bad else ''))
            out['%x' % site] = {'callee_x86': tgt[site], 'args': tr, 'bad': [b[0] for b in bad]}
    json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cs_%x.json' % fn), 'w'))
