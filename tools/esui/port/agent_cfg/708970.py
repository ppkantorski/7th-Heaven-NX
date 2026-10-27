"""tools/esui/port/agent_cfg/708970.py

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
FNS.setdefault(0x708970, {})
# materia list: row pitch imul 0x22 -> 0x18 (port: x17 lsl 1 -> x3 lsl 3), column pitch 0x82 -> 0x76 (w0 dead after ldr)
FNS[0x708970].update(
    struct_x86=XI(0x708ac9, 0x708ae8, 0x708bf2, 0x708c0c, 0x708c2b),
    arm_struct={**_R24(0xdde260), **_R24(0xdde82c),
                0xdde2d8: A.movz(0, 0x76), 0xdde2dc: A.mul(8, 8, 0),
                0xdde8a8: A.movz(0, 0x76), 0xdde8ac: A.mul(8, 8, 0)},
    store_const=[(0xdde7a4, 7)],
)
