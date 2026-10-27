"""tools/esui/port/xref.py

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
import nxmap
M=nxmap.Main(sys.argv[1]); t=M.text
inv=sorted((v,k) for k,v in M.x86_to_arm.items()); av=[v for v,k in inv]
def fn(a):
    i=bisect.bisect_right(av,a)-1; return hex(inv[i][1])
tg={}
for x in sys.argv[2:]:
    if x.startswith('x'): tg[M.x86_to_arm[int(x[1:],16)]]=x
    else: tg[int(x,16)]=x
n=len(t)//4
for i in range(n):
    w=struct.unpack_from('<I',t,i*4)[0]
    if (w&0xfc000000) in (0x94000000,0x14000000):
        off=w&0x3ffffff
        if off&0x2000000: off-=0x4000000
        d=i*4+off*4
        if d in tg: print(tg[d], 'bl' if w>>31 else 'b', 'from', hex(i*4), 'in x86 fn', fn(i*4))
for o,add in M.rel.items():
    if add in tg: print(tg[add],'reloc ptr at',hex(o))
