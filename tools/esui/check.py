"""tools/esui/check.py

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
import sys, diffemu as D, align as A, struct
COV = set()
ACOV = set()
SKIP = []


def check(fn, edits, prefixes=('00-','01-'), seeds=range(6), argsets=None, verbose=True, watch=None, native=None, pokes=None):
    D.WATCH = set(watch or [])
    D.NATIVE = set(native or [])
    COV.clear(); ACOV.clear(); SKIP.clear()
    new = A.build_new(list(prefixes))
    argsets = argsets or [[0,0,0,0],[1,2,3,0],[2,0,1,1],[3,1,0,2],[0xff,0xff,0,0],[4,4,4,4]]
    bad = 0; tot = 0
    for seed in seeds:
        D.RETMODE[0] = seed
        for args, pk in [(a_, p_) for a_ in argsets for p_ in (pokes or [{}])]:
            D.POKES = pk
            c0,w0,s0 = D.run_x86(A.exe, fn, seed, args)
            c1,w1,s1 = D.run_x86(new, fn, seed, args)
            # ARM: guest data image = new exe for both (data patches apply to both)
            d0,v0,r0 = D.run_arm({}, A.exe, fn, seed, args)
            d1,v1,r1 = D.run_arm(edits, A.exe, fn, seed, args)
            if r0 != 'ok' or s0 != 'ok':
                SKIP.append((seed, args, s0, r0)); continue
            dx = D.diff(c0,w0,c1,w1); da = D.diff(d0,v0,d1,v1)
            import difflib
            sm = difflib.SequenceMatcher(None, [t for t,_ in c0], [t for t,_ in d0], autojunk=False)
            xm = {}
            for tg,i1,i2,j1,j2 in sm.get_opcodes():
                if tg == 'equal':
                    for d in range(i2-i1): xm[i1+d] = j1+d
            def remap(e):
                if e[0] == 'call':
                    if e[1] not in xm: return ('unaligned',) + e[1:]
                    return ('call', xm[e[1]]) + e[2:]
                if e[0] == 'callseq': return None
                return e
            dx = [r for r in map(remap, dx) if r]
            da = [e for e in da if e[0] != 'callseq']
            if [t for t,_ in c0] != [t for t,_ in c1]:
                dx.append(('callseq-changed',))
            if [t for t,_ in d0] != [t for t,_ in d1]:
                da.append(('callseq-changed',))
            COV.update(D.LAST_COV); ACOV.update(D.LAST_ACOV)
            tot += 1
            st = (s0,s1,r0,r1)
            same_stock = [t for t,_ in c0]==[t for t,_ in d0]
            if dx != da or any(s!='ok' for s in st):
                bad += 1
                if verbose and bad <= 4:
                    print('seed %d args %s status %s stockseq-equal %s' % (seed, args, st, same_stock))
                    sx=set(dx); sa=set(da)
                    for e in sorted(sx-sa, key=str)[:12]: print('   x86 only', [hex(v) if isinstance(v,int) else v for v in e])
                    for e in sorted(sa-sx, key=str)[:12]: print('   arm only', [hex(v) if isinstance(v,int) else v for v in e])
            elif verbose and tot == 1:
                print('  first run: %d x86 changes, equal on ARM' % len(dx))
    want = sorted(set(watch or []))
    miss = [a for a in want if a not in COV]
    if SKIP:
        print('   %d runs skipped (stock run did not finish in the emulator): %s' % (len(SKIP), SKIP[0]))
    print('fn %x: %d/%d runs differ; changed x86 instrs covered %d/%d%s' % (fn, bad, tot, len(want) - len(miss), len(want),
          ('  MISSING ' + ' '.join('%x' % a for a in miss)) if miss else ''))
    am = sorted(a for a in edits if a not in ACOV)
    if am:
        print('   ARM edits never executed: ' + ' '.join('%x' % a for a in am))
    return bad
