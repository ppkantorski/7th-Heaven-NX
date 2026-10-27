"""tools/esui/port/dbg3.py

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
pairs, tgt, st = P.sample(fn, cfg, seeds=range(1), argsets=P.ARGSETS[:1], pokes=[None])
sites, bad = P.fit_all(pairs, tgt)
edits, end = P.build_all(cfg, sites, 0xc2d220)
xs, xn = P.exes(cfg); P.D.NATIVE = set(cfg['native']); P.D.RETMODE[0] = 0; P.D.POKES = {}
c1, w1, s1 = P.D.run_x86(xn, fn, 0, [0, 0, 0, 0]); d1, v1, r1 = P.D.run_arm(edits, P.DATA_NEW, fn, 0, [0, 0, 0, 0])
for (t, a), (u, b) in zip(c1, d1):
    if t == 0x6dfc65: print('%x %s | %x %s' % (t, [hex(x) for x in a], u, [hex(x) for x in b]))
