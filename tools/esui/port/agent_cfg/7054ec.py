"""bar/box layout (ESUI): new positions/sizes; bar width = byte * 0x3d >> 5 via cave 0x913AC0

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
FNS.setdefault(0x7054ec, {})
_m = A.movz
FNS[0x7054ec].update(
    no_align=True,
    struct_x86=ALLCH(0x7054ec),
    native_extra=[(0x913ac0, 0x913acb)],
    arm_struct={
        0xdca4d8: _m(8, 0x64),            # 0x5a, shared by both pushes (w25 copy)
        0xdca4f4: _m(8, 0x104), 0xdca5e8: _m(8, 0x100),
        0xdca6b8: _m(25, 0x12f),          # 0xf7, first use; second use below
        0xdca774: _m(27, 0x3c),           # 0x2a, both uses
        0xdca78c: _m(8, 0x104), 0xdca868: _m(8, 0x100),
        0xdca94c: _m(8, 0x1d1), 0xdcaa14: _m(8, 0x2b), 0xdcaa2c: _m(8, 0x1d1),
        0xdcaad8: _m(8, 0x12), 0xdcaaf0: _m(8, 0x164),
        0xdcab98: _m(8, 0x2b), 0xdcabb0: _m(8, 0x164),
        0xdcac68: _m(8, 0x26), 0xdcace8: _m(8, 0x4e), 0xdcad00: _m(8, 0x101),
        0xdcadb4: _m(8, 0x53), 0xdcade8: A.add_imm(19, 8, 0x117),
    },
    replace=[(0xdca748, [_m(9, 0x3d), A.mul(27, 8, 9), A.lsr(27, 27, 5)]),   # was lsl w27, w8, #1
             (0xdcac80, [_m(25, 0x101), A.str_(25, 0, 0)])],
)
