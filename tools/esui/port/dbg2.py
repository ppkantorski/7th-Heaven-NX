"""tools/esui/port/dbg2.py

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
pk = {int(k, 16): int(v, 16) for k, v in json.loads(sys.argv[2]).items()}
edits = {int(k, 16): v for k, v in json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'edits_%x.json' % fn))).items()}
xs, xn = P.exes(cfg)
P.D.NATIVE = set(cfg.get('native', ())); P.D.RETMODE[0] = 0; P.D.POKES = pk
P.D.WATCH = set(int(x, 16) for x in sys.argv[3].split(',')) if len(sys.argv) > 3 else set()
a = [0, 0, 0, 0]
r1 = P.D.run_x86(xn, fn, 0, a); print('x86 esui', r1[2], len(r1[0]), 'cov', sorted(hex(x) for x in P.D.LAST_COV))
r2 = P.D.run_arm(edits, P.EXE, fn, 0, a); print('arm edit', r2[2], len(r2[0]), 'acov', len(P.D.LAST_ACOV), [hex(x) for x in sorted(P.D.LAST_ACOV) if x < 0xc00000 or x > 0xd00000][:20])
for (t, x), (u, y) in zip(r1[0], r2[0]):
    print('%x %s | %x %s' % (t, [hex(v) for v in x[:6]], u, [hex(v) for v in y[:6]]))
