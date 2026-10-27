"""status-screen status/element text flow (ESUI): row pitch 0x1e -> 0x18, wrap limits, advances

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
FNS.setdefault(0x703df1, {})
_SUBS = lambda d, n, imm: 0x71000000 | (imm << 10) | (n << 5) | d
_MOVN = lambda d, imm: 0x12800000 | (imm << 5) | d
FNS[0x703df1].update(
    no_align=True,                      # align crosses the two loops' constants
    struct_x86=ALLCH(0x703df1),
    arm_struct={
        # case 1 (title row)
        0xdcaf80: A.sub_imm(23, 8, 0x35), 0xdcba60: A.movz(9, 0x47),
        0xdcbb48: A.movz(9, 0x90), 0xdcbc30: A.movz(9, 0xd7),
        # case 1 loop (703ee8): wrap x+w > 0x1bd, row x0x18, advance w+8
        0xdcc340: A.movz(11, 0x1bc), 0xdcc344: _SUBS(8, 9, 0x1bd),
        0xdcbcd4: A.movz(22, 0x18), 0xdcbcd8: A.movz(23, 8),
        # case 2 headers
        0xdcb194: A.sub_imm(23, 8, 0x1a), 0xdcb248: A.sub_imm(23, 23, 0), 0xdcb31c: A.sub_imm(19, 19, 0),
        # case 2 loop (7040e1): start x +0x57, wrap > 0x227, row x0x18, advance w+8
        0xdcb400: A.add_imm(8, 8, 0x57), 0xdcb79c: A.add_imm(8, 8, 0x57),
        0xdcb700: A.movz(8, 0x226), 0xdcb70c: _MOVN(8, 0x226),
        0xdcb364: A.movz(22, 0x18), 0xdcb9b0: A.add_imm(23, 8, 8),
    },
    pokes=[{0xdca488: m, 0xdca478: p} for m in (1, 2) for p in (0, 1, 2)],
    argsets=[[x, y, 0, 0] for x in (0x20, 0x120, 0x1a0) for y in (0x30, 0x90)],
)
