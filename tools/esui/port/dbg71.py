"""tools/esui/port/dbg71.py

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
fn=0x71e415; cfg=P.cfg_for(fn)
pokes = [None] + P.party_pokes() + cfg.get('pokes', []) + P.random_pokes(fn, n=12, extra=cfg.get('poke_extra', ()))[1:]
pairs, tgt, stats = P.sample(fn, cfg, seeds=range(2), argsets=P.ARGSETS[:4], pokes=pokes)
sites, bad = P.fit_all(pairs, tgt)
edits, end = P.build_all(cfg, sites, 0xc2d220)
xs,xn=P.exes(cfg)
D.NATIVE=set(cfg.get('native',())); D.NATIVE_ARM=set()
for seed in range(2):
  D.RETMODE[0]=seed
  for args in P.ARGSETS[:4]:
    for pk in pokes:
      D.POKES=pk or {}
      c1,w1,s1=D.run_x86(P.dyn(cfg,xn,pk),fn,seed,args)
      d1,v1,r1=D.run_arm(edits,P.DATA_NEW,fn,seed,args)
      if [(t,a[:5]) for t,a in c1]!=[(t,a[:5]) for t,a in d1]:
        print(seed,args,bool(pk),D.LAST_COV and 0)
        for k,(a,b) in enumerate(zip(c1,d1)): print(k,hex(a[0]),[hex(x) for x in a[1][:5]],'|',hex(b[0]),[hex(x) for x in b[1][:5]])
        sys.exit()
