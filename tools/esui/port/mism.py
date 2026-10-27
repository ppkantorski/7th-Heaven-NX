"""tools/esui/port/mism.py

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
pokes = [None] + P.party_pokes() + cfg.get('pokes', []) + ([] if cfg.get('no_random') else P.random_pokes(fn, n=12, extra=cfg.get('poke_extra', ()))[1:])
pairs, tgt, stats = P.sample(fn, cfg, seeds=range(2), argsets=cfg.get('argsets',P.ARGSETS[:4]), pokes=pokes)
sites,bad,stores=P.fit_cfg(cfg,pairs,tgt)
edits,end=P.build_all(cfg,sites,0xc2d220,stores)
D.NATIVE=set(cfg.get('native',()))|set(cfg.get('native_both',())); D.NATIVE_ARM=set(cfg.get('native_both',()))
seen=set()
for seed in range(2):
  D.RETMODE[0]=seed
  for args in cfg.get('argsets',P.ARGSETS[:4]):
    for pk in pokes:
      D.POKES=pk or {}
      c1,w1,s1=D.run_x86(P.dyn(cfg,xn,pk),fn,seed,args)
      d0,v0,r0=D.run_arm({},P.DATA_NEW,fn,seed,args); s0=list(D.LAST_SITES); wr0=list(D.LAST_WRITERS)
      d1,v1,r1=D.run_arm(edits,P.DATA_NEW,fn,seed,args); s1_=list(D.LAST_SITES)
      sm=difflib.SequenceMatcher(None,[t for t,_ in c1],[t for t,_ in d1],autojunk=False)
      for tg,i1,i2,j1,j2 in sm.get_opcodes():
        if tg!='equal': continue
        for d in range(i2-i1):
          k,q=i1+d,j1+d
          for j in range(P.arity(c1[k][0])):
            if c1[k][1][j]!=d1[q][1][j]:
              key=(s1_[q],j)
              if key in seen: continue
              seen.add(key)
              st=sites.get(s1_[q]); 
              print(hex(s1_[q]),j,hex(c1[k][0]),'x86',hex(c1[k][1][j]),'arm',hex(d1[q][1][j]),'site tr',st, 'samples',sorted(set(pairs[s1_[q]][j]))[:6])
