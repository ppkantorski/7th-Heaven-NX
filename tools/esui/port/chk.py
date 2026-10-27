"""tools/esui/port/chk.py

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
import sys,struct
sys.path.insert(0,'.')
import nso_patcher, ff7nx_esui as E
from pathlib import Path
nso=nso_patcher.read_nso(Path(sys.argv[1]))
def w(va):
    seg,off=nso_patcher.segment_for_va(nso,va,4); return struct.unpack_from('<I',seg.data,off)[0]
cnt={}
for name,lst in (('main',E.CODE_MAIN),('full',E.CODE_FULLMENU)):
    for va,old,new,fn,note in lst:
        c=w(va); k='new' if c==new else 'old' if c==old else 'other'
        cnt[(name,k)]=cnt.get((name,k),0)+1
        if k=='other': print(name,hex(va),hex(c),hex(old),hex(new))
for va,old,new,n in E.RAW_MAIN: print('raw',hex(w(va)))
print(cnt)
