"""calls a function makes on the ARM (built module): callee, first args, call site, arg writers

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
S = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, S); sys.path.insert(0, '.')
import esui_port as P
D = P.D
fn = int(sys.argv[1], 16); mod = sys.argv[2]
pokes = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
pokes = {int(k, 16): v for k, v in pokes.items()}
import preview2_pk
D.POKES = {**preview2_pk.PK, **pokes}; D.NATIVE = set(); D.NATIVE_ARM = set(); D.RETMODE[0] = 0
d, v, r = D.run_arm({}, P.DATA_NEW, fn, 0, [0, 0, 0, 0], module=mod)
print(r)
for (t, a), st, wr in zip(d, D.LAST_SITES, D.LAST_WRITERS):
    print('%x site %x args %s writers %s' % (t, st, [hex(x) if x > 0xffff else x for x in a[:4]], [hex(w) if w else w for w in wr[:3]]))
