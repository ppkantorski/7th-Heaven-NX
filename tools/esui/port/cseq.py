"""tools/esui/port/cseq.py

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
import sys, difflib; S='/private/tmp/claude-501/-Volumes-NVME-Users-ppkantorski-Downloads-ff7-mods-Claude/03970759-279c-4189-a1cb-4b17a35998c6/scratchpad'
sys.path.insert(0,S); import esui_port as P
D=P.D; fn=int(sys.argv[1],16); cfg=P.cfg_for(fn); xs,xn=P.exes(cfg)
D.NATIVE=set(cfg.get('native',()))
n=0; ARGS=[int(x,16) for x in sys.argv[2].split(',')] if len(sys.argv)>2 else [0,0,0,0]
for pk in cfg.get('pokes',[None]):
    D.POKES=pk or {}
    c0,w0,s0=D.run_x86(xs,fn,0,ARGS); c1,w1,s1=D.run_x86(P.dyn(cfg,xn,None),fn,0,ARGS)
    a=[hex(t) for t,_ in c0]; b=[hex(t) for t,_ in c1]
    if a!=b:
        print(pk, s0, s1, len(a), len(b))
        for l in list(difflib.unified_diff(a,b,lineterm='',n=1))[2:60]: print('   ',l)
        n+=1
        if n>=3: break
