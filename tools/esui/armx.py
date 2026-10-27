"""tools/esui/armx.py

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
import nxmap,capstone
M=nxmap.Main('dump/exefs/main'); a2x={v:k for k,v in M.x86_to_arm.items()}
ca=capstone.Cs(capstone.CS_ARCH_ARM64,capstone.CS_MODE_ARM)
a0,a1=int(sys.argv[1],16),int(sys.argv[2],16)
for i in ca.disasm(M.text[a0:a1],a0):
    x=''
    if i.mnemonic=='bl':
        t=int(i.op_str[1:],16); x=' ; x86 %x'%a2x[t] if t in a2x else ''
    print('  %x: %-7s %s%s'%(i.address,i.mnemonic,i.op_str,x))
