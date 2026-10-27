"""tools/esui/port/fade635.py

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
import sys, os, struct
S = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, S); sys.path.insert(0, '.')
import cemu as D
built = os.path.join(S, 'built_main'); en = open(os.path.join(S, 'built_en'), 'rb').read()
stock_en = open('dump/romfs/ff7/resources/ff7_1.02/ff7_en', 'rb').read()
fn = 0x6cf5c5
f = lambda b: struct.unpack('<f', struct.pack('<I', b))[0]
for mode in range(8):
    D.RETMODE[0] = mode
    for alpha in (0x10, 0x80, 0xe0, 0xf0, 0xff):
        D.POKES = {0xbf2de8: 1, 0x91aa8c: 1, 0xbfb2e8: alpha, 0xdc1008: 0xdd0000, 0xdd0070: 0xdd1000,
                   0x91c340: 0x01000000, 0xdc3cd8: 1, 0x91ef9c: 0x1d}
        for name, mod, buf in (('stockARM', 'dump/exefs/main', stock_en), ('built', built, en)):
            r = D.run_arm({}, buf, fn, 0, [0, 0, 0, 0], module=mod)
            W = dict(D.LAST_MEMW)
            v = lambda a: W.get(a, (0, 0, None))[2]
            if v(0xdd1000 + 4) is None:
                continue
            ys = [(f(v(0xdd1000 + 0x20 * i)), f(v(0xdd1000 + 0x20 * i + 4))) for i in range(4)]
            col = hex(v(0xdd1010) or 0)
            print(mode, hex(alpha), name, ys, 'col', col, '91c343', v(0x91c343), 'dc3cd8', v(0xdc3cd8))
    if any(True for _ in [0]):
        pass
