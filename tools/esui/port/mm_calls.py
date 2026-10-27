"""tools/esui/port/mm_calls.py

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
import preview2 as P2
fn = 0x6ca346
pk = dict(P2.pk); pk[0xdc12dc] = 1
P.D.POKES = pk; P.D.RETMODE[0] = 0; P.D.NATIVE = set()
mod = sys.argv[1]
import cemu
c, w, s = P.D.run_arm({}, P.EXE, fn, 0, [0, 0, 0, 0], module=mod)
print(s, len(c))
for (t, a), site in zip(c, P.D.LAST_SITES):
    print('%x  %x  %s' % (site, t, ' '.join('%d' % P.s32(v) for v in a[:6])))
