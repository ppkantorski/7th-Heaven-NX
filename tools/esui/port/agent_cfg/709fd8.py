"""tools/esui/port/agent_cfg/709fd8.py

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
FNS.setdefault(0x709fd8, {})
# materia screen cursor: one case per [0x920FA0]. Every change carried by hand (the port shares
# tails between cases and keeps the 0xf0 x in a per-language table, English = 0xf0)
_c = FNS[0x709fd8]
_c['no_align'] = True
_c['struct_x86'] = ALLCH(0x709fd8)
_c['arm_struct'] = {
    0xde60d4: A.movz(10, 0x3d), 0xde6108: A.movz(19, 0xee),                    # case 0
    0xde4f70: A.movz(10, 0x3d),                                                # case 1
    0xde583c: A.movz(10, 0x3d), 0xde58c4: A.movz(9, 0x18), 0xde58cc: A.add_imm(19, 8, 0xd0),
    0xde58f8: A.movz(8, 0x17c),                                                # case 3
    0xde5950: A.movz(10, 0x3d), 0xde5984: A.movz(20, 0xee), 0xde59e8: A.movz(9, 0x50),
    0xde59fc: A.sub_imm(23, 8, 0x1e), 0xde5aac: A.add_imm(23, 9, 0xd),         # case 4
    0xde4c30: A.movz(10, 0x3d), 0xde4c60: A.movz(8, 0xee),
    0xde4d28: A.movz(10, 0x76), 0xde4d2c: A.mul(8, 8, 10), 0xde4d38: A.sub_imm(23, 8, 2),   # case 5
    0xde52ac: A.movz(10, 0x3d), 0xde52dc: A.movz(8, 0xee), 0xde5390: A.sub_imm(21, 8, 2),   # case 6
    0xde5580: A.movz(10, 0x3d), 0xde55b0: A.movz(8, 0xee), 0xde5628: A.add_imm(23, 9, 0xf),  # case 7
    0xde4b40: A.movz(9, 0x18), 0xde4b48: A.add_imm(21, 8, 0xd0), 0xde4b74: A.movz(8, 0x17c),
    0xde504c: A.add_imm(21, 9, 0xd),                                           # cases 8/9
    0xde5d04: A.movz(9, 0x18), 0xde5d0c: A.add_imm(21, 8, 0xd0), 0xde5d38: A.movz(8, 0x17c),
    0xde5d98: A.movz(8, 0x16), 0xde5da8: A.add_imm(21, 9, 0x37), 0xde5de0: A.add_imm(21, 8, 0),
    0xde5ea4: A.add_imm(23, 8, 0x2d), 0xde5f2c: A.add_imm(23, 8, 0x33), 0xde5f64: A.add_imm(23, 8, 0x2d),
    0xde6024: A.add_imm(20, 8, 0x2d),                                          # case 10
}
for _a in (0xde4ccc, 0xde5348, 0xde561c):                                      # x34 -> x24
    _c['arm_struct'].update(_R24(_a))
_c['argsets'] = [[0, 0, 0, 0], [2, 0, 0, 0], [3, 0, 0, 0]]
_W = {0xdd1218: 0xdd0000, 0xdd1644: 0xdd0100, 0xdd0000 + 0x28: 0x00500030, 0xdd0000 + 0x2c: 0x00420017,
      0xdd0000 + 0x30: 0x00700060, 0xdd0000 + 0x34: 0x00130021, 0xdd0100: 0x00440021,
      0xdd0108: 0x00560034, 0xdd010c: 0x00250013, 0xdd1638: 0}
_MAT = {0xdba498 + 0x4c + 4 * k: 0x01000302 for k in range(0x20)}
_MAT.update({0xdba498 + 0x108 + 4 * k: 0x00030102 for k in range(0x120)})
_IDX = (0xdd12bc, 0xdd12f0, 0xdd12f4, 0xdd1364, 0xdd1398, 0xdd139c, 0xdd13d0, 0xdd13d4, 0xdd13e4,
        0xdd140c, 0xdd141c, 0xdd1440, 0xdd1444, 0xdd1454, 0xdd14b4, 0xdd147c, 0xdd14ec)
_c['pokes'] = [{**_W, **_MAT, 0x920fa0: cs, **{a: (i * (n + 1) + j) % 4 for n, a in enumerate(_IDX)}}
               for cs in range(11) for i, j in ((0, 0), (1, 2), (2, 1), (3, 3))]
_c['pokes'] += [{**_W, 0x920fa0: cs, **{a: 1 for a in _IDX}} for cs in range(11)]     # no materia (0xff via random? keep table)
