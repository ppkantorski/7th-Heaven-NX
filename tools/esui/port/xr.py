"""tools/esui/port/xr.py

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
import sys; sys.path[:0]=[sys.argv[0].rsplit('/',1)[0], '.']
import esui_port as P, capstone
cs=capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
a=int(sys.argv[1],16); b=int(sys.argv[2],16)
for i in cs.disasm(P.EXE[P.OFF(a):P.OFF(b)],a):
    o=P.OFF(i.address); ch = P.NEW_FULL[o:o+i.size]!=P.EXE[o:o+i.size]
    s='%s %6x  %-8s %s'%('*' if ch else ' ',i.address,i.mnemonic,i.op_str)
    if ch:
        j=next(cs.disasm(P.NEW_FULL[o:o+16],i.address)); s+='   -> %s %s'%(j.mnemonic,j.op_str)
    print(s)
