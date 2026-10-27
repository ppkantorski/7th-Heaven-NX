"""tools/esui/port/mtrace.py

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
sys.path.insert(0,S)
import esui_port as P
D=P.D
fn=int(sys.argv[1],16); watch=[int(x,16) for x in sys.argv[2].split(',')]
cfg=P.cfg_for(fn)
seen={}
D.MEMWATCH=set(watch)
for pk in cfg.get('pokes',[None]):
    D.POKES=pk or {}
    for args in cfg.get('argsets',P.ARGSETS[:2]):
        D.run_arm({},P.DATA_NEW,fn,0,args)
        for pc,acc,g,sz,v in D.LAST_MEMLOG:
            seen.setdefault((pc,acc,g,sz),set()).add(v)
for k,v in sorted(seen.items()): print('%x %s %x/%d'%k, sorted(hex(x) for x in v)[:6])
