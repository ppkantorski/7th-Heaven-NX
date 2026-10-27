"""tools/esui/port/calls.py

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
M=nxmap.Main(sys.argv[1])
A2X={v:k for k,v in M.x86_to_arm.items()}
for x in sys.argv[2:]:
    f=int(x,16); a0,a1=M.extent(f)
    out={}
    for a in range(a0,a1,4):
        w=struct.unpack_from('<I',M.img,a)[0]
        if (w&0xfc000000)==0x94000000:
            off=w&0x3ffffff
            if off&0x2000000: off-=0x4000000
            d=a+off*4
            if d in (0x10FC3A0,0xA550): continue
            out.setdefault(d,[]).append(a)
    print('fn',hex(f),hex(a0),hex(a1))
    for d,s in sorted(out.items()):
        print('   ->',hex(d), ('x86 '+hex(A2X[d])) if d in A2X else 'NATIVE/other', len(s), hex(s[0]))
