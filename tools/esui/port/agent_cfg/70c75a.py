"""materia menu: character materia list. y = i*0x1a + scroll*6 + 0xd2 -> i*0x18 + scroll*6 + 0xcc/0xcd
(two varying inputs, so the row pitch is edited: w24 = 0x1a is only used by the two row muls)

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
FNS.setdefault(0x70c75a, {})
FNS[0x70c75a]['struct_x86'] = FNS[0x70c75a].get('struct_x86', []) + XI(0x70c7f4, 0x70c806, 0x70c8f6, 0x70c908)
FNS[0x70c75a].setdefault('arm_struct', {}).update({0xdef590: A.movz(24, 0x18),
                                                  0xdef790: A.add_imm(21, 8, 0xcc),
                                                  0xdefb68: A.add_imm(21, 8, 0xcd)})
FNS[0x70c75a]['pokes'] = [{0xdd12dc + 0x38 * c: s, 0xdd12e8 + 0x38 * c: e, 0xdd12cc + 0x38 * c: s + 1}
                          for c in range(4) for s, e in ((0, 0), (3, 1), (7, 0))]
