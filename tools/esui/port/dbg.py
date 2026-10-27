"""tools/esui/port/dbg.py

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
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import esui_port as P, fns
fn = int(sys.argv[1], 16); cfg = fns.FNS[fn]
edits = {int(k, 16): v for k, v in json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'edits_%x.json' % fn))).items()}
xs, xn = P.exes(cfg)
P.D.NATIVE=set(cfg.get('native',()));P.D.RETMODE[0] = 0; P.D.POKES = {}
args = [0, 0, 0, 0]
for nm, r in (('x86 stock', P.D.run_x86(P.EXE, fn, 0, args)), ('x86 esui', P.D.run_x86(xn, fn, 0, args)),
              ('arm stock', P.D.run_arm({}, P.EXE, fn, 0, args)), ('arm edit', P.D.run_arm(edits, P.EXE, fn, 0, args))):
    print(nm, r[2], len(r[0]), ' '.join('%x' % t for t, _ in r[0][:50]))
