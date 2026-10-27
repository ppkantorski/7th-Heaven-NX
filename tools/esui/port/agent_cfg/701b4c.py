"""bar/box layout (ESUI): new positions; bar width = byte * 0x3d >> 5 via cave 0x913AC0

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
FNS.setdefault(0x701b4c, {})
_m = A.movz
FNS[0x701b4c].update(
    no_align=True,
    struct_x86=ALLCH(0x701b4c),
    native_extra=[(0x913ac0, 0x913acb)],
    arm_struct={
        0xdbf3cc: _m(8, 0xc), 0xdbf43c: A.add_imm(21, 8, 0x58), 0xdbf468: _m(8, 0x1ee),
        0xdbf540: A.add_imm(24, 8, 0x58), 0xdbf56c: _m(8, 0x1ea),
        0xdbf664: _m(25, 0x259),          # both pushes
        0xdbf82c: A.add_imm(25, 8, 0x1b9), 0xdbf914: A.add_imm(23, 8, 0x1b9),
        0xdbfa0c: A.add_imm(8, 8, 0x169), 0xdbfba0: A.add_imm(8, 8, 0x169),
    },
    replace=[(0xdbf410, [_m(9, 0x3d), A.mul(21, 8, 9), A.lsr(21, 21, 5)])],     # was lsl w21, w8, #1
)
