"""tools/esui/port/adrpref.py

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
import sys,struct; sys.path.insert(0,'.')
import nxmap, capstone
M=nxmap.Main(sys.argv[1]); t=M.text
tgt=int(sys.argv[2],16); page=tgt&~0xfff; lo=tgt&0xfff
md=capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
n=len(t)//4
for i in range(n):
    w=struct.unpack_from('<I',t,i*4)[0]
    if (w&0x9f000000)==0x90000000:
        immlo=(w>>29)&3; immhi=(w>>5)&0x7ffff
        imm=((immhi<<2)|immlo)
        if imm&(1<<20): imm-=1<<21
        pc=i*4
        if ((pc&~0xfff)+(imm<<12))==page:
            rd=w&31
            for k in range(1,6):
                w2=struct.unpack_from('<I',t,(i+k)*4)[0]
                ins=list(md.disasm(struct.pack('<I',w2),(i+k)*4))
                if not ins: continue
                s=ins[0].op_str
                if ('x%d,'%rd in s or '[x%d'%rd in s) and ('#0x%x'%lo in s):
                    print(hex((i+k)*4), ins[0].mnemonic, s); break
