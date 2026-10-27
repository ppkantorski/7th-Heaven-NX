"""tools/esui/port/cs715.py

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
import sys, difflib; sys.path[:0]=[sys.argv[0].rsplit('/',1)[0], '.']
import esui_port as P, fns
D=P.D
fn=int(sys.argv[1],16); cfg=P.cfg_for(fn)
xs,xn=P.exes(cfg)
D.NATIVE=set(cfg['native'])|set(cfg.get('native_both',())); D.NATIVE_ARM=set(cfg.get('native_both',()))
pk = eval(sys.argv[2]) if len(sys.argv)>2 else None
D.POKES=pk or {}; D.RETMODE[0]=0
c0,_,s0=D.run_x86(xs,fn,0,[0,0,0,0]); c1,_,s1=D.run_x86(xn,fn,0,[0,0,0,0])
d0,_,r0=D.run_arm(cfg.get('arm_struct',{}),P.DATA_NEW,fn,0,[0,0,0,0])
print(s0,s1,r0,len(c0),len(c1),len(d0))
a=[hex(t) for t,_ in c0]; b=[hex(t) for t,_ in c1]
for l in difflib.unified_diff(a,b,lineterm='',n=2): print(l)
