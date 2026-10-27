"""tools/esui/port/dbg4.py

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
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import esui_port as P
fn = int(sys.argv[1], 16); cfg = P.cfg_for(fn)
pokes = [None] + P.party_pokes()
pairs, tgt, st = P.sample(fn, cfg, seeds=range(1), argsets=P.ARGSETS[:2], pokes=pokes)
sites, bad = P.fit_all(pairs, tgt)
edits, end = P.build_all(cfg, sites, 0xc2d220)
xs, xn = P.exes(cfg); P.D.NATIVE = set(cfg['native'])
for seed in range(2):
  for args in P.ARGSETS[:4]:
    for pk in pokes:
        P.D.RETMODE[0] = seed; P.D.POKES = pk or {}
        c0, w0, s0 = P.D.run_x86(P.DATA_NEW, fn, seed, args); c1, w1, s1 = P.D.run_x86(xn, fn, seed, args)
        d0, v0, r0 = P.D.run_arm({}, P.DATA_NEW, fn, seed, args); d1, v1, r1 = P.D.run_arm(edits, P.DATA_NEW, fn, seed, args)
        mx = {a for a in set(w0) | set(w1) if w0.get(a) != w1.get(a)}
        ma = {a for a in set(v0) | set(v1) if v0.get(a) != v1.get(a)}
        if mx != ma:
            print('seed', seed, args, pk and {hex(k): v for k, v in pk.items() if k in (0xdc3c7c, 0xdc2088)})
            for a in sorted(mx | ma)[:12]: print('   %x x86 %s->%s arm %s->%s' % (a, w0.get(a), w1.get(a), v0.get(a), v1.get(a)))
            sys.exit()
