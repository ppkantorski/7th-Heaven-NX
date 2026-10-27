"""tools/esui/peek.py

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
exe=open('dump/romfs/ff7/resources/ff7_1.02/ff7_en','rb').read(); pe=exe_patch.parse_pe(exe)
M=nxmap.Main('dump/exefs/main')
va=int(sys.argv[1],16)
fs,fe=M.containing(va); a0,a1=M.extent(fs)
print('x86 fn %X-%X  arm %X-%X (%d words)'%(fs,fe,a0,a1,(a1-a0)//4))
x=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_32)
o=exe_patch.va_to_offset(pe,fs)
for i in x.disasm(exe[o:o+fe-fs],fs):
    if abs(i.address-va)<int(sys.argv[2]) : print('  x86 %X %s %s'%(i.address,i.mnemonic,i.op_str), '<==' if i.address<=va<i.address+i.size else '')
a=capstone.Cs(capstone.CS_ARCH_ARM64,capstone.CS_MODE_ARM)
want=sys.argv[3] if len(sys.argv)>3 else None
for i in a.disasm(M.text[a0:a1],a0):
    if want and want in i.op_str: print('  arm %X %s %s'%(i.address,i.mnemonic,i.op_str))
