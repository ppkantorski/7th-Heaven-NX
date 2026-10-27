"""Item menu (715105)

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
FNS.setdefault(0x715105, {})
_c = FNS[0x715105]
# item list visible rows: [ebp-0x38] = (flag != 0) + 0xa -> + 0xf (loop bound of both item loops)
_c['struct_x86'] = _c.get('struct_x86', []) + XI(0x71577b)
_c.setdefault('arm_struct', {})[0xe156dc] = A.add_imm(21, 8, 0xf)
# item rows: y = i*0x25 + scroll*9 + K -> i*0x18 + scroll*9 + K-2 (the port keeps 0x25 in w21 for all four)
_c['struct_x86'] += XI(0x71582a, 0x71583c, 0x7158f4, 0x715906, 0x715929, 0x71593b, 0x71595f, 0x715971)
_c['arm_struct'].update({0xe15738: A.movz(21, 0x18), 0xe15a78: A.add_imm(28, 8, 0x6b),
                         0xe15db4: A.add_imm(25, 8, 0x67), 0xe15e9c: A.add_imm(25, 8, 0x70),
                         0xe15fa8: A.add_imm(20, 8, 0x6e)})
# every sub-state ([DD19C8]), cursor rows / scrolls varied independently
_c['pokes'] = [{0xdc130c: 0, 0xdd1b68: 0, 0xdd1a50: 0, 0xdd18c0: 0xdd0000, 0xdd19c8: s,
                0xdd1a54: a, 0xdd1a64: a2, 0xdd1a18: b, 0xdd1a14: c, 0xdd1b44: d, 0xdd1b54: e, 0xdd1b34: f,
                0xdd1b6c: 1, 0xdd1afc: g, 0xdd1ac4: h, 0xdd1ac0: k, 0xdd1ae4: e, 0xdd1a3c: d, 0xdd1a74: f, 0xdd1ad4: 0}
               for s in range(6)
               for a, a2, b, c, d, e, f, g, h, k in ((0, 0, 0, 0, 0, 0, 0, 0, 0, 0), (3, 1, 1, 2, 1, 3, 4, 2, 1, 1),
                                                     (5, 4, 2, 4, 3, 1, 2, 5, 3, 0), (1, 2, 0, 6, 2, 2, 6, 1, 2, 1))]
# [ebp-0x30] = [DD1A14]*0x25 - [DD1B44]*0x25 + [DD1B54]*9 - 9: ESUI changes only the first 0x25 -> 0x18
# (the port: mul w8, w8, w20 with w20 = 0x25 for both); then y + 0x71 -> + 0x78
_c['struct_x86'] += XI(0x71569d, 0x7156ce)
_c['replace'] = _c.get('replace', []) + [(0xe153c8, [A.add_reg_lsl(8, 8, 8, 1), A.lsl(8, 8, 3)])]
_c['arm_struct'][0xe154d8] = A.add_imm(20, 8, 0x78)
# key items rows: y = i*0x24 + [DD1AE4]*9 + 0x7c -> i*0x18 (the port: add w8,w8,w8,lsl #3 ; lsl #2)
_c['struct_x86'] += XI(0x715a6a)
_c['arm_struct'].update(_R24(0xe152cc))
