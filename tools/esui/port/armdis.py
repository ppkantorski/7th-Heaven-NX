"""tools/esui/port/armdis.py

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
import sys; sys.path.insert(0,'.')
import nxmap, capstone
M=nxmap.Main(sys.argv[1])
md=capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
a=int(sys.argv[2],16); n=int(sys.argv[3])
for i in md.disasm(M.img[a:a+4*n], a):
    print('%8x  %s %s'%(i.address,i.mnemonic,i.op_str))
