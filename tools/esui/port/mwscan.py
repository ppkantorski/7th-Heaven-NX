"""tools/esui/port/mwscan.py

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
import sys,struct,bisect; sys.path.insert(0,'.')
import nxmap, capstone
M=nxmap.Main(sys.argv[1]); t=M.text
inv=sorted((v,k) for k,v in M.x86_to_arm.items()); av=[v for v,k in inv]
md=capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
n=len(t)//4
W=struct.unpack('<%dI'%n,t[:n*4])
def is_cmp(w,imm): # cmp wN,#imm  = subs wzr,wN,#imm
    return (w&0xffc0001f)==0x7100001f and ((w>>10)&0xfff)==imm
hits=[]
for i in range(n):
    if is_cmp(W[i],0x2d):
        win=W[i:i+12]
        if any(is_cmp(w,0x37) for w in win) and any(is_cmp(w,0x4d) for w in win):
            a=i*4; j=bisect.bisect_right(av,a)-1
            hits.append((a,inv[j][1]))
for a,f in hits: print(hex(a),'x86 fn',hex(f))
