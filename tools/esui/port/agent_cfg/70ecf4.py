"""tools/esui/port/agent_cfg/70ecf4.py

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
FNS.setdefault(0x70ecf4, {})
# shared 70f491 call (both cases push 0xd -> 0xe), list row y x36 -> x24 (+0x11 -> +0x17), x -0x12 -> +6, 6eb3b8 arg 0xf -> 0xc
FNS[0x70ecf4]['struct_x86'] = XI(0x70eef9, 0x70ef58, 0x70ef80, 0x70ef83, 0x70ef92, 0x70f02a)
FNS[0x70ecf4]['arm_struct'] = {0xdf7290: A.movz(8, 0xe), 0xdf6b34: A.add_imm(22, 8, 0x17),
                               0xdf6b6c: A.add_imm(22, 8, 6), 0xdf70b4: A.movz(8, 0xc), **_R24(0xdf6b24)}
# reach every case: [dca814] 0/2 cursor, [dd1664] 0/1/2 sub-window, [dd155c] 0/1 column, slots set
_MSL = {0xdbfd8c + 0x40 + 4 * k: 0x00000005 + k for k in range(0x10)}
FNS[0x70ecf4]['argsets'] = [[0, 0, 0, 0], [2, 0, 0, 0]]
FNS[0x70ecf4]['pokes'] = [{**_MSL, 0xdd1648: 0, 0xdd164c: 0, 0xdd1678: 1, 0xdca814: cm, 0xdd1664: w, 0xdd155c: col,
                           0xdd1640: a + 2, 0xdd166c: b, 0xdd12a0: a, 0xdd1670: b, 0xdd1668: 0, 0xdd1558: a,
                           0xdd1524: b, 0xdd1534: a, 0xdd1594: a + b, 0xdd12a4: b, 0xdc04b4 + 4 * (a + b): 0x12}
                          for cm in (0, 2) for w in (0, 1, 2) for col in (0, 1) for a, b in ((0, 0), (1, 2), (3, 1))]
