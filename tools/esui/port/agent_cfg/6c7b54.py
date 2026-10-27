"""post-battle gil/items screen (6C7B54). ESUI's cave 0x9138D9: edx = (byte[99E2D4+i*12] * 0x3d) >> 5
(stock: * 2); the port's lsl w24, w8, #1 at cf4d9c becomes mov/mul/lsr

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
FNS.setdefault(0x6c7b54, {})
FNS[0x6c7b54]['struct_x86'] = FNS[0x6c7b54].get('struct_x86', []) + XI(0x6c8358, 0x6c835e)
FNS[0x6c7b54]['native_extra'] = [(0x9138d9, 0x9138e4)]
FNS[0x6c7b54]['replace'] = [(0xcf4d9c, [A.movz(9, 0x3d), A.mul(24, 8, 9), A.lsr(24, 24, 5)])]
