"""tools/esui/xdiff.py

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
import sys; sys.path.insert(0,'.'); sys.path.insert(0,'tools/esui')
from cands import *
fs=int(sys.argv[1],16); fs,fe=M.containing(fs)
lo=int(sys.argv[2],16) if len(sys.argv)>2 else fs; hi=int(sys.argv[3],16) if len(sys.argv)>3 else fe
A=list(cx.disasm(exe[off(fs):off(fe)],fs))
for i in A:
    if not lo<=i.address<hi: continue
    nb=new[off(i.address):off(i.address)+i.size]; ob=exe[off(i.address):off(i.address)+i.size]
    if nb!=ob:
        n=next(cx.disasm(nb,i.address),None)
        print('* %x: %-28s -> %s'%(i.address,i.mnemonic+' '+i.op_str,(n.mnemonic+' '+n.op_str) if n else '??'))
    else: print('  %x: %s %s'%(i.address,i.mnemonic,i.op_str))
