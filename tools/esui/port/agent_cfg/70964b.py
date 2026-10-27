"""tools/esui/port/agent_cfg/70964b.py

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
FNS.setdefault(0x70964b, {})
# materia list rows: imul 0x22 -> 0x18 (port: x17 lsl 1 -> x3 lsl 3)
FNS[0x70964b].update(struct_x86=XI(0x709770), arm_struct=_R24(0xde22a8))
