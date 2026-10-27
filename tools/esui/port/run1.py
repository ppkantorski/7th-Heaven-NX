"""tools/esui/port/run1.py

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
pairs, tgt, stats = P.sample(fn, cfg)
print(dict(stats))
sites, bad = P.fit_all(pairs, tgt)
for s, (c, tr) in sorted(sites.items()): print('  %x -> %x %s' % (s, c, tr))
for s, b in bad.items(): print('  BAD %x %s' % (s, b))
