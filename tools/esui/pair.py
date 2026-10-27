"""tools/esui/pair.py

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
import exe_patch, capstone, nxmap
EXE='dump/romfs/ff7/resources/ff7_1.02/ff7_en'
exe=open(EXE,'rb').read(); pe=exe_patch.parse_pe(exe)
M=nxmap.Main('dump/exefs/main')
cx=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_32); ca=capstone.Cs(capstone.CS_ARCH_ARM64,capstone.CS_MODE_ARM)
s=int(sys.argv[1],16); s,e=M.containing(s); a0,a1=M.extent(s)
o=exe_patch.va_to_offset(pe,s)
for i in cx.disasm(exe[o:o+e-s],s): print('  %x: %-8s %s'%(i.address,i.mnemonic,i.op_str))
print('---- arm %x..%x'%(a0,a1))
for i in ca.disasm(M.text[a0:a1],a0): print('  %x: %-8s %s'%(i.address,i.mnemonic,i.op_str))
