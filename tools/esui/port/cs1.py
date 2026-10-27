"""tools/esui/port/cs1.py

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
S = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, S); sys.path.insert(0, '.')
import esui_port as P
BUILT = os.path.join(S, 'built_main'); BEN = open(os.path.join(S, 'built_en'), 'rb').read()
fn = 0x71aaa3; cfg = P.cfg_for(fn); xs, xn = P.exes(cfg)
P.D.NATIVE = set(cfg.get('native', ())); P.D.NATIVE_ARM = set()
import itertools
for s, k, c, r in itertools.product((2,), (0,), (0, 1), (0, 1, 3, 7, 14)):
    pk = {0x92565c: s, 0xdc130c: 0, 0xdd4734: 1, 0xdd4724: k, 0xdd6bb8: c, 0xdd6bbc: r, 0xdd6bcc: 0, 0xdd6be8: 0}
    for args in cfg.get('argsets', P.ARGSETS[:4])[:2]:
        P.D.POKES = pk; P.D.RETMODE[0] = 0
        f = lambda c: [tuple(a[:2]) for t, a in c if t == 0x6eb3b8]
        try:
            c0 = f(P.D.run_x86(P.DATA_NEW, fn, 0, args)[0]); c1 = f(P.D.run_x86(P.dyn(cfg, xn, pk), fn, 0, args)[0])
            d0 = f(P.D.run_arm({}, P.DATA_NEW, fn, 0, args)[0]); d1 = f(P.D.run_arm({}, BEN, fn, 0, args, module=BUILT)[0])
        except Exception as e:
            print('err', e); continue
        print(s, k, c, r, args[:2], 'x86', c0, c1, 'arm', d0, d1)
