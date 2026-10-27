"""tools/esui/port/x86dis.py

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
import exe_patch, capstone
buf=open(sys.argv[1],'rb').read()
pe=exe_patch.parse_pe(buf)
def off(va):
    for name,sva,vs,ro,rs in pe['sections']:
        if sva<=va<sva+rs: return ro+va-sva
md=capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
a=int(sys.argv[2],16); n=int(sys.argv[3])
o=off(a)
for k,i in enumerate(md.disasm(buf[o:o+n*8],a)):
    if k>=n: break
    print('%8x  %s %s'%(i.address,i.mnemonic,i.op_str))
