"""materia menu: check/arrange list. y = i*0x24 + 0x74 + scroll*9 -> i*0x18 + 0x7a + scroll*9 (two
varying inputs: the x36 row pitch, add w8,w8,w8,lsl #3 ; lsl #2, becomes x24)

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
FNS.setdefault(0x70f2e9, {})
FNS[0x70f2e9]['struct_x86'] = FNS[0x70f2e9].get('struct_x86', []) + XI(0x70f37c, 0x70f45e)
FNS[0x70f2e9].setdefault('arm_struct', {}).update({**_R24(0xdff070), **_R24(0xdff3f4)})
_ML = {0xdc04b4 + 4 * k: (0xffffffff if k % 5 == 3 else k + 1) for k in range(0x30)}
FNS[0x70f2e9]['pokes'] = [{**_ML, 0xdd1544: s, 0xdd1534: st, 0xdd1550: e}
                          for s in (0, 3, 7) for st in (0, 5) for e in (0, 1)]
