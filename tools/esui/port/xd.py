"""tools/esui/port/xd.py

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
import sys; S='/private/tmp/claude-501/-Volumes-NVME-Users-ppkantorski-Downloads-ff7-mods-Claude/03970759-279c-4189-a1cb-4b17a35998c6/scratchpad'
sys.path.insert(0,S); sys.path.insert(0,'.'); import esui_port as P, capstone
md=capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
fn=int(sys.argv[1],16); ctx=int(sys.argv[2]) if len(sys.argv)>2 else 6
a,b=P.M.containing(fn); o=P.OFF(a)
ins=list(md.disasm(P.EXE[o:P.OFF(b)],a))
ch=[k for k,i in enumerate(ins) if P.EXE[P.OFF(i.address):P.OFF(i.address)+i.size]!=P.NEW_FULL[P.OFF(i.address):P.OFF(i.address)+i.size]]
show=set()
for k in ch: show.update(range(max(0,k-ctx),min(len(ins),k+3)))
last=-2
for k in sorted(show):
    i=ins[k]; so=P.OFF(i.address)
    if k!=last+1: print('   ...')
    last=k
    if k in ch:
        j=next(md.disasm(P.NEW_FULL[so:so+16],i.address)); print('*%8x  %-38s -> %s %s'%(i.address,i.mnemonic+' '+i.op_str,j.mnemonic,j.op_str))
    else: print(' %8x  %s %s'%(i.address,i.mnemonic,i.op_str))
