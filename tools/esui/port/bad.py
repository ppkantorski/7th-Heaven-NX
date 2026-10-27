"""tools/esui/port/bad.py

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
import esui_port as P, fns
fn = int(sys.argv[1], 16); cfg = fns.FNS[fn]
pokes = P.random_pokes(fn, n=int(sys.argv[2]), extra=cfg.get('poke_extra', ()))
pairs, tgt, stats = P.sample(fn, cfg, seeds=range(3), pokes=pokes)
sites, bad = P.fit_all(pairs, tgt)
for s, b in bad.items():
    for j, sm in b:
        print('%x -> %x arg %d: (x86old,x86new,armold) %s' % (s, tgt[s], j, [tuple(hex(v) for v in t) for t in sorted(set(pairs[s][j]))[:6]]))
